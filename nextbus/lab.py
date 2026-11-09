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
    discarded = set(read(LAB/'discarded_examples.json', []))
    cases = read(ROOT/'examples.json', {}).get('cases', []) + read(LAB/'examples.json', [])
    return [case for case in cases if case['case_id'] not in discarded]


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


def default_dispatchers():
    examples = [e['case_id'] for e in available_examples()[:4]]
    defaults = [dict(id='preset-base0',name='BASE0',kind='BASE0',memory=0,example_ids=[],protected=True),
                dict(id='preset-base1',name='BASE1',kind='RULE',memory=0,example_ids=[],
                     crowd_level='HIGH',rule_share=.7,minimum_buses=2,baseline=True)]
    for letter,memory,fewshot in [('A',0,False),('B',30,False),('C',0,True),('D',30,True)]:
        defaults.append(dict(id='preset-llm'+letter.lower(),name='LLM-'+letter,kind='LLM',memory=memory,
                             example_ids=list(examples) if fewshot else [],model=DEFAULT_MODEL,
                             endpoint=DEFAULT_ENDPOINT,context_tokens=1048576))
    return defaults


class Lab:
    def __init__(self, data, config):
        self.data, self.config = data, config
        self.lock = RLock()
        self.job = {'status': 'ready'}
        self.profiles = read(LAB/'dispatchers.json', None)
        self.experiments = read(LAB/'experiments.json', [])
        if self.profiles is None:
            self.profiles = default_dispatchers()
            # Preserve the old combined profiles as separate dispatcher and experiment records.
            for old in read(LAB/'profiles.json', []):
                dispatcher = {k:v for k,v in old.items() if k not in ('days','mode','start','end','seed')}
                if dispatcher['kind']=='BASE1':
                    dispatcher.update(kind='RULE',baseline=True,crowd_level='HIGH',rule_share=.7,minimum_buses=2)
                self.profiles.append(dispatcher)
                self.experiments.append(dict(id='legacy-'+old['id'],name=old['name'],dispatcher_id=old['id'],
                    days=old['days'],mode=old['mode'],start=old['start'],end=old['end'],seed=old['seed']))
            self.save_catalogs()
        renamed = False
        for p in self.profiles:
            if p['id'] in {'preset-llm'+c.lower() for c in 'ABCD'} and p['name']=='LLM'+p['id'][-1].upper():
                p['name'] = 'LLM-'+p['id'][-1].upper()
                renamed = True
        if renamed: self.save_catalogs()
        if not any(p['id']=='preset-base0' for p in self.profiles):
            self.profiles.insert(0,default_dispatchers()[0])
            self.save_catalogs()

    def save_catalogs(self):
        save_json(LAB/'dispatchers.json',self.profiles)
        save_json(LAB/'experiments.json',self.experiments)

    def status(self):
        with self.lock:
            rows = read(LAB/'results.json', [])
            dispatchers = {p['id']:p for p in self.profiles}
            current = {e['id']: self.run_profile(e, dispatchers[e['dispatcher_id']])
                       for e in self.experiments if e['dispatcher_id'] in dispatchers}
            for row in rows:
                profile = current.get(row.get('experiment_id'))
                row['replay_current'] = profile is not None and row.get('profile') == profile
                row.setdefault('experiment_name',row.get('dispatcher','Previous experiment'))
            return dict(job=self.job.copy(), profiles=self.profiles, experiments=self.experiments,
                        results=rows, examples=available_examples(),
                        default_model=DEFAULT_MODEL, default_endpoint=DEFAULT_ENDPOINT)

    def validate_dispatcher(self, p):
        kind = p.get('kind','LLM')
        if kind not in ('BASE0','RULE','LLM'): raise ValueError('Choose LLM or rule-based')
        name = str(p.get('name','')).strip()[:100]
        if not name: raise ValueError('Dispatcher name is required')
        result = dict(id=str(p.get('id') or uuid4().hex),name=name,kind=kind,memory=0,example_ids=[])
        if kind=='LLM':
            memory = int(p.get('memory',30))
            context = int(p.get('context_tokens',1048576 if p.get('model',DEFAULT_MODEL)==DEFAULT_MODEL else 32768))
            if not 0<=memory<=120 or memory%15: raise ValueError('Memory must be 0–120 minutes in 15-minute steps')
            if not 16384<=context<=2000000: raise ValueError('Context budget must be 16384–2000000 tokens')
            examples = p.get('example_ids',[])
            available = {e['case_id'] for e in available_examples()}
            if not isinstance(examples,list) or any(e not in available for e in examples): raise ValueError('Unknown example')
            endpoint = str(p.get('endpoint',DEFAULT_ENDPOINT)).strip()
            model = str(p.get('model',DEFAULT_MODEL)).strip()
            if not endpoint.startswith('https://'): raise ValueError('Provider endpoint must use HTTPS')
            if not model: raise ValueError('Model is required')
            result.update(memory=memory,context_tokens=context,example_ids=list(dict.fromkeys(examples)),model=model,endpoint=endpoint)
        elif kind=='RULE':
            level = p.get('crowd_level','HIGH')
            share = float(p.get('rule_share',.7))
            minimum = int(p.get('minimum_buses',2))
            if level not in ('LOW','MEDIUM','HIGH') or not math.isfinite(share) or not 0<share<=1 or not 1<=minimum<=1000:
                raise ValueError('Choose a crowd level, a share above 0 and at most 100%, and at least one observed bus')
            result.update(crowd_level=level,rule_share=share,minimum_buses=minimum,baseline=False)
        return result

    def profile(self, payload):
        with self.lock:
            if payload.get('id')=='preset-base0' or payload.get('kind')=='BASE0':
                raise ValueError('BASE0 is the fixed scheduled-service dispatcher')
            profile = self.validate_dispatcher(payload)
            # Editing the historical BASE1 creates a custom rule, never rewrites the baseline policy.
            if profile['id']=='preset-base1' or any(p['id']==profile['id'] and p.get('baseline') for p in self.profiles):
                profile['id'] = uuid4().hex
                if profile['name']=='BASE1': profile['name']='BASE1 custom'
            self.profiles = self.upsert(self.profiles, profile)
            self.save_catalogs()
            return profile

    def validate_experiment(self, p):
        dispatcher = next((d for d in self.profiles if d['id']==p.get('dispatcher_id')),None)
        if dispatcher is None: raise ValueError('Select a saved dispatcher; it may have been discarded')
        name = str(p.get('name','')).strip()[:100]
        if not name: raise ValueError('Experiment name is required')
        days = p.get('days',['2026-08-24'])
        if not isinstance(days,list) or not days or any(d not in self.data.days() for d in days):
            raise ValueError('Choose valid teaching days')
        mode = p.get('mode','day')
        if mode not in ('day','window'): raise ValueError('Unknown test mode')
        start,end = (int(self.config.start),int(self.config.end)) if mode=='day' else (int(p.get('start',420)),int(p.get('end',570)))
        if not self.config.start<=start<end<=self.config.end or start%15 or end%15:
            raise ValueError('Choose a valid window on 15-minute boundaries')
        available = {e['case_id']:e for e in available_examples()}
        if any(e not in available for e in dispatcher['example_ids']): raise ValueError('Dispatcher contains an unavailable example')
        if any(available[e]['day'] in days for e in dispatcher['example_ids']): raise ValueError('Example days and test days must be disjoint')
        return dict(id=str(p.get('id') or uuid4().hex),name=name,dispatcher_id=dispatcher['id'],
                    days=list(dict.fromkeys(days)),mode=mode,start=start,end=end,seed=int(p.get('seed',101)))

    def experiment(self, payload):
        with self.lock:
            experiment = self.validate_experiment(payload)
            self.experiments = self.upsert(self.experiments, experiment)
            self.save_catalogs()
            return experiment

    @staticmethod
    def upsert(records, record):
        if any(item['id']==record['id'] for item in records):
            return [record if item['id']==record['id'] else item for item in records]
        return [*records, record]

    @staticmethod
    def run_profile(experiment, dispatcher):
        return dict(dispatcher,days=experiment['days'],mode=experiment['mode'],start=experiment['start'],
                    end=experiment['end'],seed=experiment['seed'],experiment_id=experiment['id'],
                    experiment_name=experiment['name'])

    def start(self, payload):
        with self.lock:
            if self.job['status'] in ('running','warming'): raise ValueError('An experiment is already running')
            saved = next((e for e in self.experiments if e['id']==payload.get('experiment_id')),None)
            if saved is None: raise ValueError('Save and select an experiment first')
            experiment = self.validate_experiment(saved)
            dispatcher = next(d for d in self.profiles if d['id']==experiment['dispatcher_id'])
            profile = self.run_profile(experiment, dispatcher)
            key = str(payload.get('api_key','')).strip() or os.environ.get(self.config.llm_key_env,'')
            if profile['kind']=='LLM' and not key: raise ValueError('Enter and save an API key in Dispatcher Setup')
            self.job = dict(status='warming',name=experiment['name'],experiment_id=experiment['id'],time=self.config.start)
        Thread(target=self.run,args=(profile,key),daemon=True).start()
        return {'started':True,'experiment_id':experiment['id']}

    def run(self, profile, key):
        try:
            cfg, data, frozen = load_setup()
            examples = [c for c in available_examples() if c['case_id'] in profile['example_ids']]
            completed = []
            for day in profile['days']:
                run_id = uuid4().hex
                folder = LAB/'runs'/run_id
                folder.mkdir(parents=True)
                cfg = replace(cfg, day=day, seed=profile['seed'], arm='BASE0', history_minutes=60,
                              llm_model=profile.get('model',DEFAULT_MODEL), llm_endpoint=profile.get('endpoint',DEFAULT_ENDPOINT))
                sim = ExperimentSimulation(data,cfg)
                start,end = profile['start'],profile['end']
                with self.lock:
                    self.job = dict(status='warming',id=run_id,day=day,time=sim.t,name=profile['experiment_name'],experiment_id=profile['experiment_id'],frames=0)
                # BASE0 processes every event strictly before the checkpoint.
                before_events(sim,start)
                opening = totals(sim)
                sim.hourly = []
                sim.hour_opening, sim.hour_start = opening, start
                if profile['kind']=='RULE':
                    from .dispatch_rules import CustomRule
                    sim.controller = NetworkRule() if profile.get('baseline') else CustomRule(profile['crowd_level'],profile['rule_share'],profile['minimum_buses'])
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
                        self.job = dict(status='running',id=run_id,day=day,time=t,end=end,name=profile['experiment_name'],experiment_id=profile['experiment_id'],frames=frames)
                metrics = difference(opening,totals(sim),cfg.capacity)
                row = dict(id=run_id,experiment_id=profile['experiment_id'],experiment_name=profile['experiment_name'],dispatcher=profile['name'],day=day,start=start,end=end,seed=profile['seed'],
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
                completed.append(row)
            with self.lock:
                rows = read(LAB/'results.json',[])
                # Publish the whole experiment together, replacing obsolete test days too.
                rows = [r for r in rows if r.get('experiment_id')!=profile['experiment_id']]
                save_json(LAB/'results.json',rows+completed)
                self.job['status']='complete'
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
            if payload.get('type')=='example':
                if self.job['status'] in ('running','warming'):
                    raise ValueError('Wait for the current job before discarding examples')
                example_id = payload['id']
                if example_id not in {e['case_id'] for e in available_examples()}:
                    raise ValueError('Unknown example')
                discarded = set(read(LAB/'discarded_examples.json', []))
                discarded.add(example_id)
                save_json(LAB/'discarded_examples.json', sorted(discarded))
                self.profiles = [dict(p, example_ids=[e for e in p['example_ids'] if e != example_id])
                                 for p in self.profiles]
                self.save_catalogs()
            elif payload.get('type')=='experiment':
                if self.job['status'] in ('running','warming') and self.job.get('experiment_id')==payload['id']:
                    raise ValueError('Wait for this experiment to finish before discarding it')
                self.experiments=[e for e in self.experiments if e['id']!=payload['id']]
                self.save_catalogs()
            elif payload.get('type')=='profile':
                if payload['id']=='preset-base0': raise ValueError('BASE0 cannot be discarded')
                self.profiles=[p for p in self.profiles if p['id']!=payload['id']]
                self.save_catalogs()
            else:
                rows=read(LAB/'results.json',[])
                save_json(LAB/'results.json',[r for r in rows if r['id']!=payload['id']])
        return {'discarded':True}

    def csv(self):
        output=io.StringIO(newline='')
        rows=[]
        for r in read(LAB/'results.json',[]):
            row={'experiment_name':r.get('experiment_name',r.get('dispatcher','Previous experiment'))}
            row.update({k:r[k] for k in ('id','dispatcher','kind','model','day','start','end','seed','memory','example_count')})
            row.update(r['metrics'])
            row['example_details']=json.dumps([dict(day=e['day'],start=e['time'],minutes=e['horizon_minutes']) for e in r['examples']])
            # Prevent spreadsheet formulas in user-provided labels.
            rows.append({k:("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v) for k,v in row.items()})
        if rows:
            writer=csv.DictWriter(output,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        return output.getvalue().encode('utf-8-sig')
