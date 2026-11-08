"""Configurable local experiments. Credentials remain in process memory only."""
import csv
import gzip
import io
import json
import math
import os
from dataclasses import asdict, replace
from pathlib import Path
from threading import RLock, Thread
from uuid import uuid4

from .experiments import save_json
from .protocol import (ROOT, ExperimentLLM, ExperimentSimulation, NetworkRule,
                       before_events, difference, load_setup, totals)

LAB = Path('outputs/lab')
DEFAULT_MODEL = 'gemini-3.5-flash-lite'
DEFAULT_ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'


def read(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def available_examples():
    return read(ROOT/'examples.json', {}).get('cases', []) + read(LAB/'examples.json', [])


class BoundedLLM(ExperimentLLM):
    def __init__(self, config, examples, key, context_tokens):
        super().__init__(config, examples)
        self.key, self.context_tokens = key, context_tokens

    def input(self, sim):
        payload = super().input(sim)
        if self.config.arm in ('LLM-B','LLM-D'):
            from .protocol import history
            payload['history'] = history(sim,self.config.history_minutes)
            payload['dispatch_events'] = [e for e in sim.dispatch_log if sim.t-self.config.history_minutes<=e['time']<sim.t]
            for example in payload.get('examples',[]):
                checkpoint=example['snapshot']['time']
                example['history']=[h for h in example.get('history',[]) if checkpoint-self.config.history_minutes<=h['time']<checkpoint]
        # UTF-8 bytes are a conservative upper bound for byte-based tokenizers.
        # Reserve 8192 tokens for instructions, framing, and the short response.
        size = len(json.dumps(payload).encode('utf-8'))
        if size + 8192 > self.context_tokens:
            raise ValueError('Prompt exceeds conservative context budget. Reduce examples or memory, or set the documented model context limit.')
        return payload

    def decide(self, sim):
        # A unique environment name isolates parallel simulator/provider settings.
        name = 'NEXTBUS_RUN_' + uuid4().hex
        self.config = replace(self.config, llm_key_env=name)
        os.environ[name] = self.key
        try:
            return super().decide(sim)
        finally:
            os.environ.pop(name, None)


class Lab:
    def __init__(self, data, config):
        self.data, self.config = data, config
        self.lock = RLock()
        self.job = {'status': 'ready'}
        self.profiles = read(LAB/'profiles.json', [])

    def status(self):
        with self.lock:
            return dict(job=self.job.copy(), profiles=self.profiles,
                        results=read(LAB/'results.json', []),
                        examples=available_examples(),
                        default_model=DEFAULT_MODEL, default_endpoint=DEFAULT_ENDPOINT)

    def profile(self, payload):
        profile = self.validate(payload)
        with self.lock:
            self.profiles = [p for p in self.profiles if p['id'] != profile['id']] + [profile]
            save_json(LAB/'profiles.json', self.profiles)
        return profile

    def validate(self, p):
        memory = int(p.get('memory', 30))
        context = int(p.get('context_tokens', 1048576 if p.get('model',DEFAULT_MODEL)==DEFAULT_MODEL else 32768))
        start, end = int(p.get('start', 360)), int(p.get('end', 1440))
        days = p.get('days', ['2026-08-24'])
        if not days or not isinstance(days,list) or any(d not in self.data.days() for d in days):
            raise ValueError('Choose valid teaching days')
        if not 0 <= memory <= 120 or memory % 15:
            raise ValueError('Memory must be 0–120 minutes in 15-minute steps')
        if not 16384 <= context <= 2000000:
            raise ValueError('Context budget must be 16384–2000000 tokens')
        if not self.config.start <= start < end <= self.config.end or start % 15 or end % 15:
            raise ValueError('Choose a valid window on 15-minute boundaries')
        examples = p.get('example_ids', [])
        available = {c['case_id']:c for c in available_examples()}
        if not isinstance(examples,list) or any(e not in available for e in examples):
            raise ValueError('Unknown example')
        if any(available[e]['day'] in days for e in examples):
            raise ValueError('Example days and test days must be disjoint')
        endpoint = str(p.get('endpoint', DEFAULT_ENDPOINT)).strip()
        if not endpoint.startswith('https://'):
            raise ValueError('Provider endpoint must use HTTPS')
        arm = p.get('kind','LLM')
        if arm not in ('BASE0','BASE1','LLM'): raise ValueError('Unknown dispatcher type')
        name = str(p.get('name','My dispatcher')).strip()[:100]
        if not name: raise ValueError('Name is required')
        mode = p.get('mode','day')
        if mode not in ('day','window'): raise ValueError('Unknown test mode')
        return dict(id=str(p.get('id') or uuid4().hex), name=name, kind=arm,
                    model=str(p.get('model',DEFAULT_MODEL)).strip(), endpoint=endpoint,
                    memory=memory, context_tokens=context, days=days, mode=mode,
                    start=start if mode=='window' else int(self.config.start),
                    end=end if mode=='window' else int(self.config.end),
                    seed=int(p.get('seed',101)), example_ids=list(dict.fromkeys(examples)))

    def start(self, payload):
        profile = self.validate(payload)
        key = str(payload.get('api_key','')).strip() or os.environ.get(self.config.llm_key_env,'')
        if profile['kind']=='LLM' and (not key or not profile['model']):
            raise ValueError('Supply an API key and model')
        with self.lock:
            if self.job['status'] in ('running','warming'): raise ValueError('An experiment is already running')
            self.job = dict(status='warming', name=profile['name'], time=self.config.start)
        Thread(target=self.run, args=(profile,key), daemon=True).start()
        return {'started':True}

    def run(self, profile, key):
        try:
            cfg, data, frozen = load_setup()
            examples = [c for c in available_examples() if c['case_id'] in profile['example_ids']]
            for day in profile['days']:
                run_id = uuid4().hex
                folder = LAB/'runs'/run_id
                folder.mkdir(parents=True)
                cfg = replace(cfg, day=day, seed=profile['seed'], arm='BASE0', history_minutes=60,
                              llm_model=profile['model'], llm_endpoint=profile['endpoint'])
                sim = ExperimentSimulation(data,cfg)
                start,end = profile['start'],profile['end']
                with self.lock:
                    self.job = dict(status='warming',id=run_id,day=day,time=sim.t,name=profile['name'],frames=0)
                # BASE0 processes every event strictly before the checkpoint.
                before_events(sim,start)
                opening = totals(sim)
                sim.hourly = []
                sim.hour_opening, sim.hour_start = opening, start
                if profile['kind']=='BASE1': sim.controller = NetworkRule()
                elif profile['kind']=='LLM':
                    arm = 'LLM-D' if examples and profile['memory'] else 'LLM-C' if examples else 'LLM-B' if profile['memory'] else 'LLM-A'
                    sim.controller = BoundedLLM(replace(cfg,arm=arm,history_minutes=profile['memory']),examples,key,profile['context_tokens'])
                frames = 0
                boundaries = [start] + list(range(start+5,end,5)) + [end]
                for t in boundaries:
                    before_events(sim,t)
                    frame = sim.snapshot()
                    frame['evaluation'] = difference(opening,totals(sim),cfg.capacity)
                    frame['decisions'] = sim.decision_log[-10:]
                    # gzip keeps full individual map frames affordable on disk.
                    with gzip.open(folder/f'{frames}.json.gz','wt',encoding='utf-8') as f:
                        json.dump(frame,f,allow_nan=False)
                    frames += 1
                    with self.lock:
                        self.job = dict(status='running',id=run_id,day=day,time=t,end=end,name=profile['name'],frames=frames)
                metrics = difference(opening,totals(sim),cfg.capacity)
                row = dict(id=run_id,dispatcher=profile['name'],day=day,start=start,end=end,seed=profile['seed'],
                           memory=profile['memory'],model=profile['model'] if profile['kind']=='LLM' else '',
                           kind=profile['kind'],example_count=len(examples),examples=examples,profile=profile,
                           frames=frames,metrics=metrics)
                save_json(folder/'manifest.json', dict(profile=profile,day=day,config=asdict(cfg),
                          workbook_sha256=data.digest,calibration=frozen,checkpoint_source='BASE0',
                          objective='balanced-five-metrics-v1',frame_interval_minutes=5))
                save_json(folder/'boarding_events.json',sim.boarding_events)
                save_json(folder/'rule_audit.json',sim.rule_audit)
                save_json(folder/'dispatches.json',sim.dispatch_log)
                save_json(folder/'llm_calls.json',getattr(sim.controller,'calls',[]))
                with self.lock:
                    rows = read(LAB/'results.json',[])
                    rows.append(row)
                    save_json(LAB/'results.json',rows)
            with self.lock: self.job['status']='complete'
        except Exception as exc:
            with self.lock: self.job.update(status='failed',error=str(exc))

    def frame(self, run_id, index):
        if len(run_id)!=32 or any(c not in '0123456789abcdef' for c in run_id) or index<0:
            raise ValueError('Invalid replay identifier')
        with gzip.open(LAB/'runs'/run_id/f'{index}.json.gz','rt',encoding='utf-8') as f:
            return json.load(f)

    def example(self, payload):
        day = payload['day']
        start, minutes = int(payload['start']), int(payload.get('minutes',60))
        if day not in self.data.days() or start%15 or not 15<=minutes<=120 or minutes%15 or not self.config.start<=start<start+minutes<=self.config.end:
            raise ValueError('Choose a teaching day and a 15–120 minute example inside service hours')
        with self.lock:
            if self.job['status'] in ('running','warming'): raise ValueError('Wait for the current job')
            self.job=dict(status='warming',name='Generating example',time=self.config.start)
        Thread(target=self.generate_example,args=(day,start,minutes),daemon=True).start()
        return {'started':True}

    def generate_example(self, day, start, minutes):
        try:
            cfg,data,_=load_setup()
            cfg=replace(cfg,day=day,seed=201,arm='BASE0')
            sim=ExperimentSimulation(data,cfg)
            before_events(sim,start)
            outcomes=[]
            for policy in ('BASE0','BASE1'):
                branch=sim.clone()
                branch.controller=NetworkRule() if policy=='BASE1' else None
                before=totals(branch)
                before_events(branch,start+minutes)
                outcomes.append(dict(policy=policy,horizon_minutes=minutes,decisions=branch.decision_log,
                                     metrics_60m=difference(before,totals(branch),cfg.capacity)))
            from .protocol import history
            case=dict(case_id='custom-'+uuid4().hex[:12],day=day,seed=201,time=start,
                      snapshot=sim.observation(),history=history(sim,120),dispatch_events=[],
                      candidate_outcomes=outcomes,preferred_action_index=None,horizon_minutes=minutes)
            with self.lock:
                cases=read(LAB/'examples.json',[])
                cases.append(case)
                save_json(LAB/'examples.json',cases)
                self.job=dict(status='complete',name='Example ready')
        except Exception as exc:
            with self.lock: self.job=dict(status='failed',error=str(exc))

    def discard(self, payload):
        with self.lock:
            if payload.get('type')=='profile':
                self.profiles=[p for p in self.profiles if p['id']!=payload['id']]
                save_json(LAB/'profiles.json',self.profiles)
            else:
                rows=read(LAB/'results.json',[])
                save_json(LAB/'results.json',[r for r in rows if r['id']!=payload['id']])
        return {'discarded':True}

    def csv(self):
        output=io.StringIO(newline='')
        rows=[]
        for r in read(LAB/'results.json',[]):
            row={k:r[k] for k in ('id','dispatcher','kind','model','day','start','end','seed','memory','example_count')}
            row.update(r['metrics'])
            row['example_details']=json.dumps([dict(day=e['day'],start=e['time'],minutes=e['horizon_minutes']) for e in r['examples']])
            # Prevent spreadsheet formulas in user-provided labels.
            rows.append({k:("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v) for k,v in row.items()})
        if rows:
            writer=csv.DictWriter(output,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        return output.getvalue().encode('utf-8-sig')
