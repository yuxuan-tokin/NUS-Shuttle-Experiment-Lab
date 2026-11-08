import argparse
from dataclasses import replace
import json
from pathlib import Path
from .config import Config
from .data import Dataset
from .experiments import run, calibrate, compare, counterfactual, freeze_examples, representative_days, save_json
from .simulation import Simulation


def main():
    parser = argparse.ArgumentParser(description='NUS individual-level shuttle simulator')
    parser.add_argument('--config')
    sub = parser.add_subparsers(dest='command',required=True)
    sub.add_parser('init')
    sub.add_parser('audit')
    single = sub.add_parser('run')
    single.add_argument('--output',default='outputs/runs/demo')
    server = sub.add_parser('serve')
    server.add_argument('--port',type=int,default=8765)
    fit = sub.add_parser('calibrate')
    fit.add_argument('--candidates')
    fit.add_argument('--screen',action='store_true')
    fit.add_argument('--screen-days',help='Comma-separated valid dates for a screening run')
    fit.add_argument('--attendance-diagnostics',action='store_true')
    fit.add_argument('--seeds',default='1,2,3')
    fit.add_argument('--output',default='outputs/calibration.json')
    case = sub.add_parser('case')
    case.add_argument('--time',type=float,required=True)
    case.add_argument('--route',required=True)
    case.add_argument('--output',required=True)
    freeze = sub.add_parser('freeze-examples')
    freeze.add_argument('--cases',nargs=4,required=True)
    freeze.add_argument('--test-days',required=True)
    freeze.add_argument('--output',default='outputs/examples.json')
    experiment = sub.add_parser('compare')
    experiment.add_argument('--days',required=True)
    experiment.add_argument('--seeds',default='1')
    experiment.add_argument('--arms',default='BASE0,BASE1,LLM-A,LLM-B,LLM-C,LLM-D')
    experiment.add_argument('--output',default='outputs/experiments')
    experiment.add_argument('--smoke',action='store_true')
    args = parser.parse_args()
    c = Config.load(args.config)
    if args.command=='init':
        c.save('config.json')
        print('Created config.json with provisional research defaults')
        return
    if args.command=='freeze-examples':
        freeze_examples(args.cases,args.output,args.test_days.split(','))
        return
    d = Dataset(c)
    if args.command=='audit':
        counts = [(day,len(d.sessions(day)[0]),len(d.sessions(day)[1])) for day in d.days()]
        result = dict(places=len(d.places),routes=list(d.routes),days=counts,warnings=d.warnings)
        save_json('outputs/input_audit.json',result)
        print(json.dumps(result,indent=2))
    elif args.command=='run':
        print(json.dumps(run(d,c,args.output).metrics(),indent=2))
    elif args.command=='serve':
        from .server import serve
        serve(d,c,args.port)
    elif args.command=='calibrate':
        candidates = json.loads(Path(args.candidates).read_text(encoding='utf-8')) if args.candidates else [{}]
        screen = args.screen or args.screen_days is not None
        days = args.screen_days.split(',') if args.screen_days else representative_days(d) if screen else d.days()
        result = calibrate(d,c,candidates,days,[int(x) for x in args.seeds.split(',')],args.output,
                           screen=screen,diagnostics=args.attendance_diagnostics)
        print(json.dumps(dict(frozen=result['frozen'],chosen=result['chosen'])))
    elif args.command=='case':
        from .controllers import controller
        sim = Simulation(d,c)
        sim.controller = controller(c)
        # Stop before the controller decision at the case timestamp.
        sim.events = [event for event in sim.events if not(event[3]=='decide' and event[0]>=args.time)]
        import heapq
        heapq.heapify(sim.events)
        sim.advance(args.time)
        save_json(args.output,counterfactual(sim,args.route))
    elif args.command=='compare':
        compare(d,c,args.days.split(','),[int(x) for x in args.seeds.split(',')],args.arms.split(','),args.output,not args.smoke)


if __name__=='__main__':
    main()
