from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
import errno
import subprocess
import sys
from pathlib import Path
from threading import RLock
from urllib.parse import urlsplit
from .config import Config
from .data import Dataset
from .controllers import controller
from .experiments import save_json
from .simulation import Simulation


def experiment_running(process, root):
    if process is not None and process.poll() is None:
        return True
    import msvcrt
    try:
        with (root/'job.lock').open('r+b') as lock:
            try:
                msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            except OSError as exc:
                if exc.errno == errno.EACCES:
                    return True
                raise
            msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
    except FileNotFoundError:
        pass
    return False


def make_server(data,config,port=8765):
    from .lab import Lab
    from .protocol import ExperimentSimulation, NetworkRule, ExperimentLLM
    lab = Lab(data,config)
    state = {'sim':None,'experiment':None}
    lock = RLock()
    class Handler(BaseHTTPRequestHandler):
        def handle(self):
            try:
                super().handle()
            except ConnectionError:
                # Closing/reloading a browser tab may disconnect mid-response.
                pass

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def end_headers(self):
            self.send_header('Cache-Control','no-store')
            super().end_headers()

        def reply(self,value,status=200):
            body = json.dumps(value,allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            assets = {'/':('experiments.html','text/html; charset=utf-8'),'/map-view':('index.html','text/html; charset=utf-8'),'/style.css':('style.css','text/css'),'/app.js':('app.js','text/javascript')}
            assets.update({'/experiments':('experiments.html','text/html; charset=utf-8'),'/experiments.js':('experiments.js','text/javascript; charset=utf-8')})
            if path in assets:
                filename,content_type = assets[path]
                body = Path(__file__).with_name('web').joinpath(filename).read_bytes()
                self.send_response(200)
                self.send_header('Content-Type',content_type)
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path=='/api/lab':
                self.reply(lab.status())
            elif path=='/api/lab/csv':
                body=lab.csv()
                self.send_response(200)
                self.send_header('Content-Type','text/csv; charset=utf-8')
                self.send_header('Content-Disposition','attachment; filename=nextbus-results.csv')
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path=='/api/experiments':
                from .protocol import ROOT, TEST_DAYS, TEST_SEEDS, TRAINING, PERIODS, ARMS
                def read(name,default):
                    file = ROOT/name
                    return json.loads(file.read_text(encoding='utf-8')) if file.exists() else default
                current = Config.load('config.json')
                running = experiment_running(state['experiment'], ROOT)
                progress = read('progress.json',{'status':'ready'})
                if not running and progress['status'] in ('starting','running'):
                    progress = {'status':'interrupted','error':'Start the job again to resume from saved results.'}
                self.reply(dict(days=TEST_DAYS,seeds=TEST_SEEDS,training=TRAINING,periods=PERIODS,arms=ARMS,
                    progress=progress,rows=read('results.json',[]),
                    summary=read('summary.json',[]),examples=read('examples.json',read('examples_draft.json',{})),
                    endpoint=current.llm_endpoint,model=current.llm_model,key_present=bool(os.environ.get(current.llm_key_env)),
                    running=running))
            elif path=='/api/experiments/csv':
                file = Path('outputs/dispatch_experiment/results.csv')
                body = file.read_bytes() if file.exists() else b''
                self.send_response(200)
                self.send_header('Content-Type','text/csv')
                self.send_header('Content-Disposition','attachment; filename=results.csv')
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path=='/api/health':
                self.reply({'app':'nextbus','api_version':1})
            elif path=='/api/setup':
                self.reply(dict(config=asdict(config),days=data.days(),places=[asdict(p) for p in data.places],routes=data.routes,destinations=data.destinations,facilities=data.facilities,warnings=data.warnings))
            elif path=='/api/snapshot':
                with lock:
                    self.reply(state['sim'].snapshot() if state['sim'] else {})
            else:
                self.reply({'error':'Unknown endpoint'},404)

        def do_POST(self):
            # Local UI only. Reject browser requests from other origins.
            actual_port = self.server.server_port
            if self.headers.get('Origin') not in (None,f'http://127.0.0.1:{actual_port}',f'http://localhost:{actual_port}'):
                self.reply({'error':'Origin rejected'},403)
                return
            # Serve static files concurrently; serialize changes to the simulation.
            with lock:
                self.mutate()

        def mutate(self):
            try:
                length = int(self.headers.get('Content-Length',0))
                if length>100000: raise ValueError('Request too large')
                payload = json.loads(self.rfile.read(length) or b'{}')
                if self.path.startswith('/api/lab/'):
                    command=self.path.rsplit('/',1)[-1]
                    if command=='profile': result=lab.profile(payload)
                    elif command=='start': result=lab.start(payload)
                    elif command=='discard': result=lab.discard(payload)
                    elif command=='frame': result=lab.frame(str(payload['id']),int(payload['index']))
                    elif command=='example': result=lab.example(payload)
                    else: raise ValueError('Unknown lab command')
                    self.reply(result)
                    return
                if self.path=='/api/experiments/run':
                    from .protocol import ROOT, TEST_DAYS, TEST_SEEDS, ARMS
                    day,seed,arm = payload.get('day'),payload.get('seed'),payload.get('arm')
                    if day not in TEST_DAYS or seed not in TEST_SEEDS or arm not in ARMS:
                        raise ValueError('Unknown evaluation run')
                    folder = ROOT/'runs'/f'{day}_{seed}_{arm}'
                    result = {}
                    for name in ('rows','dispatches','manifest','llm_calls'):
                        file = folder/(name+'.json')
                        if file.exists(): result[name] = json.loads(file.read_text(encoding='utf-8'))
                    self.reply(result)
                    return
                if self.path=='/api/experiments/start':
                    from .protocol import ROOT, load_setup
                    task = payload.get('task')
                    if task not in ('examples','baselines','evaluate'): raise ValueError('Unknown experiment task')
                    if experiment_running(state['experiment'], ROOT):
                        raise ValueError('An experiment job is already running. Start another job when it finishes.')
                    cfg,_,_ = load_setup()
                    if task=='evaluate' and (not cfg.llm_endpoint or not cfg.llm_model or not os.environ.get(cfg.llm_key_env)):
                        raise ValueError('Set the provider, model and API key environment variable first')
                    ROOT.mkdir(parents=True,exist_ok=True)
                    with (ROOT/'job.log').open('ab') as log:
                        state['experiment'] = subprocess.Popen([sys.executable,'-m','nextbus.protocol',task],
                            stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    self.reply({'started':task})
                    return
                if self.path=='/api/experiments/provider':
                    from .protocol import ROOT
                    if experiment_running(state['experiment'], ROOT):
                        raise ValueError('Wait for the running job before changing provider settings')
                    current = Config.load('config.json')
                    current.llm_endpoint = str(payload.get('endpoint','')).strip()
                    current.llm_model = str(payload.get('model','')).strip()
                    current.save('config.json')
                    self.reply({'saved':True})
                    return
                if self.path=='/api/reset':
                    c = Config(**payload).validate()
                    if c.workbook!=config.workbook: raise ValueError('Restart server to select another workbook')
                    if c.arm.startswith('LLM') and (not c.llm_endpoint or not c.llm_model or not os.environ.get(c.llm_key_env)):
                        raise ValueError(f'Configure llm_endpoint, llm_model and environment variable {c.llm_key_env} before creating an LLM run')
                    run_data = Dataset(c)
                    sim = ExperimentSimulation(run_data,c)
                    sim.controller = controller(c)
                    state['sim'] = sim
                sim = state['sim']
                if sim is None: raise ValueError('Create a run first')
                if self.path=='/api/step':
                    minutes = float(payload.get('minutes',5))
                    if not math.isfinite(minutes) or not 0<minutes<=60:
                        raise ValueError('Step must be greater than zero and at most 60 minutes')
                    sim.advance(min(sim.config.end,sim.t+minutes))
                elif self.path=='/api/export':
                    folder = Path('outputs/ui_run')
                    save_json(folder/'config.json',asdict(sim.config))
                    save_json(folder/'metrics.json',sim.metrics())
                    save_json(folder/'timeseries.json',sim.series)
                    save_json(folder/'decisions.json',sim.decision_log)
                    save_json(folder/'attendance.json',sim.attendance)
                    save_json(folder/'dispatches.json',sim.dispatch_log)
                    save_json(folder/'manifest.json',dict(workbook_sha256=sim.data.digest,config=asdict(sim.config),warnings=sim.data.warnings,completed=sim.t>=sim.config.end))
                    if hasattr(sim.controller,'calls'): save_json(folder/'llm_calls.json',sim.controller.calls)
                elif self.path not in ('/api/reset','/api/step'):
                    raise ValueError('Unknown endpoint')
                result = sim.snapshot()
                if self.path=='/api/reset':
                    result['map'] = dict(places=[asdict(p) for p in sim.data.places],routes=sim.data.routes,destinations=sim.data.destinations,facilities=sim.data.facilities)
                result['decisions'] = sim.decision_log[-10:]
                # snapshot includes a fresh BASE1 congestion diagnostic for all arms
                result['llm_input'] = sim.controller.input(sim) if hasattr(sim.controller,'input') else None
                self.reply(result)
            except Exception as exc:
                self.reply({'error':str(exc)},400)

    return ThreadingHTTPServer(('127.0.0.1',port),Handler)


def serve(data,config,port):
    with make_server(data,config,port) as server:
        print(f'Open http://127.0.0.1:{server.server_port} in your browser. Ctrl+C stops the server.',flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
