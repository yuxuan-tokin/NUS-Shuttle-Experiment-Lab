"""Frozen-calibration dispatch experiment specified in the November brief."""
import argparse
import copy
import csv
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

from .config import Config
from .controllers import RuleController
from .data import Dataset
from .experiments import save_json
from .simulation import Simulation

ROOT = Path('outputs/dispatch_experiment')
ARMS = ('BASE0','BASE1','LLM-A','LLM-B','LLM-C','LLM-D')
TEST_DAYS = ('2026-08-24','2026-08-26')
TEST_SEEDS = (101,)
PERIODS = (('Morning',420,570),('Noon',660,810),('Afternoon',840,990),
           ('Evening',1020,1170),('Night',1230,1380))
TRAINING = (('2027-01-25',480),('2027-02-10',720),('2027-03-05',1080),('2027-03-16',1290))


def history(sim, minutes=60):
    return [x for x in sim.history if sim.t-minutes<=x['time']<sim.t and x['time']%15==0]


def validate_action(action, routes):
    if not isinstance(action,dict) or set(action)!={'routes'}:
        raise ValueError('Action must contain only routes')
    selected = action['routes']
    if not isinstance(selected,list) or any(not isinstance(x,str) or x not in routes for x in selected):
        raise ValueError('Unknown route')
    if len(set(selected))!=len(selected):
        raise ValueError('At most one new bus per route')
    return selected


class NetworkRule(RuleController):
    def decide(self,sim):
        # Reuse the rule criteria, restricting its evidence to 15-minute samples.
        saved = sim.history
        sim.history = history(sim,30)
        try:
            super().decide(sim)
        finally:
            sim.history = saved
        selected = [x['route'] for x in self.details if x['share'] is not None
                    and x['crowded']>=sim.config.rule_min_crowded and x['share']>=sim.config.rule_share
                    and not x['pending'] and sim.t-self.last.get(x['route'],-1e9)>=sim.config.rule_cooldown]
        # The legacy rule records its first selected route before returning.
        selected += [r for r,t in self.last.items() if t==sim.t and r not in selected]
        for route in selected:
            self.last[route] = sim.t
        return dict(routes=sorted(selected))


class ExperimentSimulation(Simulation):
    def __init__(self,data,config):
        super().__init__(data,config)
        self.attempts = self.denials = 0
        self.boarding_events = []
        self.rule_audit = []
        self.hourly = []
        self.hour_opening = totals(self)
        self.hour_start = self.t

    def launch(self,route,index,extra=False):
        bus_id = super().launch(route,index,extra)
        if extra:
            # A matching route/time dispatch gets matching delay draws across branches.
            uid = f'extra-{route}-{self.t:g}'
            self.buses[bus_id]['id'] = uid
            self.dispatch_log[-1]['bus_id'] = uid
        return bus_id

    def board(self,bus_id,index):
        bus = self.buses[bus_id]
        stop = self.data.routes[bus['route']][index]
        attempts = sum(self.leg[i][2]==bus['route'] and self.leg[i][3]==index for i in self.queues[stop])
        self.attempts += attempts
        denied = max(0,attempts-(self.config.capacity-len(bus['passengers'])))
        self.denials += denied
        if attempts:
            self.boarding_events.append(dict(time=self.t, route=bus['route'], stop=stop, stop_name=self.data.places[stop].name, run_id=bus['id'], load_before=len(bus['passengers']), attempts=attempts, denied=denied))
        super().board(bus_id,index)

    def observe(self):
        super().observe()
        if hasattr(self, 'hour_opening') and self.t > self.hour_start and self.t % 60 == 0:
            current = totals(self)
            self.hourly.append(dict(start=self.hour_start, end=self.t,
                                    **difference(self.hour_opening,current,self.config.capacity)))
            self.hour_opening, self.hour_start = current, self.t

    def snapshot(self):
        value = super().snapshot()
        rule = RuleController() if isinstance(self.controller,RuleController) and not isinstance(self.controller,NetworkRule) else NetworkRule()
        if isinstance(self.controller, RuleController):
            rule.last = self.controller.last.copy()
        rule.decide(self)
        hourly = list(self.hourly)
        if self.t > self.hour_start:
            hourly.append(dict(start=self.hour_start,end=self.t,**difference(self.hour_opening,totals(self),self.config.capacity)))
        value.update(rule=[dict(x,congested=x['share'] is not None and x['crowded']>=self.config.rule_min_crowded and x['share']>=self.config.rule_share) for x in rule.details], dispatches=self.dispatch_log, hourly=hourly,
                     evaluation=difference({k:0 for k in totals(self)},totals(self),self.config.capacity))
        return value

    def execute(self,action):
        if isinstance(action,dict) and 'dispatch' in action:
            return super().execute(action)
        routes = validate_action(action,self.data.routes)
        self.decision_log.append(dict(time=self.t,action=action,valid=True))
        if isinstance(self.controller,NetworkRule):
            self.rule_audit.append(dict(time=self.t,routes=copy.deepcopy(self.controller.details),action=action))
        for route in routes:
            self.launch(route,0,True)
        return True


def totals(sim):
    service = passengers = extra_service = extra_passengers = 0.
    for bus in sim.buses:
        duration,occupied,_ = sim.bus_metrics(bus)
        service += duration
        passengers += occupied
        if bus['extra']:
            extra_service += duration
            extra_passengers += occupied
    return dict(wait=sim.wait_total,attempts=sim.attempts,denials=sim.denials,
                service=service,passengers=passengers,extra_service=extra_service,
                extra_passengers=extra_passengers,dispatches=len(sim.dispatch_log))


def difference(before,after,capacity):
    d = {k:after[k]-before[k] for k in before}
    return dict(total_wait=d['wait'],capacity_failure_pct=100*d['denials']/d['attempts'] if d['attempts'] else None,
                boarding_attempts=d['attempts'],denied_attempts=d['denials'],
                overall_utilization_pct=100*d['passengers']/(capacity*d['service']) if d['service'] else None,
                extra_utilization_pct=100*d['extra_passengers']/(capacity*d['extra_service']) if d['extra_service'] else None,
                extra_buses=d['dispatches'],extra_bus_hours=d['extra_service']/60)


def before_decision(sim,t):
    # Drain boundary events, then sample before the boundary dispatch decision.
    import heapq
    decisions = [e for e in sim.events if e[0]==t and e[3]=='decide']
    sim.events = [e for e in sim.events if e not in decisions]
    heapq.heapify(sim.events)
    sim.advance(t)
    for event in decisions:
        heapq.heappush(sim.events,event)


def before_events(sim,t):
    import heapq
    pending = [e for e in sim.events if e[0]>=t]
    sim.events = [e for e in sim.events if e[0]<t]
    heapq.heapify(sim.events)
    sim.advance(t)
    for event in pending:
        heapq.heappush(sim.events,event)


class ExperimentLLM:
    def __init__(self,config,examples,call_log=None,legacy_endpoint=None,*,balanced=True):
        self.config,self.examples,self.calls = config,examples,[]
        self.balanced = balanced
        self.call_log = call_log
        self.legacy_endpoint = legacy_endpoint
        self.saved_calls = (
            json.loads(call_log.read_text(encoding='utf-8'))
            if call_log is not None and call_log.exists() else []
        )

    def input(self,sim):
        memory = self.config.arm in ('LLM-B','LLM-D')
        payload = dict(current=sim.observation(),network=sim.data.routes,
                       stops=[dict(id=i,xy=p.xy) for i,p in enumerate(sim.data.places[:sim.data.stop_count])],
                       depots=sim.data.depots)
        payload['segment_travel_minutes'] = {
            route: [sim.data.segment_times[a,b] for a,b in zip(stops,stops[1:])]
            for route,stops in sim.data.routes.items()
        }
        payload['depot_to_first_stop_minutes'] = {
            route: round(float(sim.data.distances[sim.data.depots[route],stops[0]])
                         * sim.config.road_multiplier / sim.config.depot_speed_kmh * 60, 2)
            for route,stops in sim.data.routes.items()
        }
        if memory:
            payload['history'] = history(sim)
            payload['dispatch_events'] = [e for e in sim.dispatch_log if sim.t-60<=e['time']<sim.t]
        if self.config.arm in ('LLM-C','LLM-D'):
            keys = ['case_id','snapshot','candidate_outcomes','preferred_action_index','horizon_minutes']
            if memory:
                keys += ['history','dispatch_events']
            payload['examples'] = [{k:c[k] for k in keys} for c in self.examples]
        return payload

    def decide(self,sim):
        objective = (
            'Consider all five evaluation metrics together: total waiting passenger-minutes, '
            'capacity failure rate (denied eligible boarding attempts / all eligible attempts), '
            'overall fleet utilization (passenger-minutes / capacity-service-minutes), '
            'extra buses dispatched, and extra passenger-service bus-hours. '
            'Balance passenger service against resource use over the next 60 minutes. '
            'No metric has fixed priority; no weights, scoring formula, or tie-breaking hierarchy is supplied. '
            'Utilization is an efficiency signal, not a target to maximize at the expense of denied boardings. '
            'Consider travel time and service already assigned before choosing each extra bus. '
        )
        example_preference = (
            'Historical preferred action indices used a waiting-first ranking; they are not instructions. '
            'Assess the recorded outcomes across all five metrics using your own judgment. '
        )
        instructions = (
            'You dispatch supplemental buses for a simulated university shuttle network with seven routes. '
            'Every 15 minutes, decide which routes, if any, should receive one additional bus now. '
            'Scheduled buses and previously dispatched buses continue operating. New buses travel from their depot '
            'to the first stop, serve the route in order, and return. Allow for the time needed to reach the route. '
            'The current snapshot shows vehicles, their route progress, categorical crowding, and outstanding '
            'extra bus commitments. Network entries are ordered stop IDs. Coordinates are latitude and longitude '
            'and times are minutes since midnight. Use LOW, MEDIUM, and HIGH as ordinal crowding signals. '
            'For each route, segment_travel_minutes lists nominal travel times between consecutive stops in '
            'route order. depot_to_first_stop_minutes gives the estimated time before a new bus starts service. '
            'Use these durations and current bus positions to estimate when service can reach downstream stops. '
            'When history is present, use the observed snapshots and dispatch events to assess changes in service. '
            f'{objective}'
            'When examples are present, they compare two policies from the same simulated checkpoint over their stated horizon_minutes. '
            'BASE0 keeps scheduled service and sends no new buses. BASE1 applies a rule-based dispatch policy every 15 minutes. Each branch lists its decisions and measured results. '
            f'{example_preference}'
            'Return only a JSON object with one key, routes. Its value is a list of distinct route IDs from the '
            'network, one new bus per listed route. Return {"routes":[]} to dispatch no extra buses.'
        )
        payload = self.input(sim)
        body = dict(model=self.config.llm_model,temperature=self.config.llm_temperature,
                    messages=[dict(role='system',content=instructions),dict(role='user',content=json.dumps(payload))],
                    response_format=dict(type='json_object'))
        index = len(self.calls)
        if index < len(self.saved_calls):
            saved = self.saved_calls[index]
            if (saved.get('time') == sim.t and saved.get('request') == body
                    and saved.get('endpoint', self.legacy_endpoint) == self.config.llm_endpoint
                    and 'action' in saved):
                validate_action(saved['action'],sim.data.routes)
                self.calls.append(dict(saved,endpoint=self.config.llm_endpoint))
                return saved['action']
            # A changed request or a missing decision invalidates the rest of the replay.
            self.saved_calls = []
        record = dict(time=sim.t,input=payload,request=body,attempts=[],endpoint=self.config.llm_endpoint)
        self.calls.append(record)
        for attempt in range(3):
            started = time.monotonic()
            try:
                request = urllib.request.Request(self.config.llm_endpoint,data=json.dumps(body).encode(),
                    headers={'Content-Type':'application/json','Authorization':'Bearer '+os.environ[self.config.llm_key_env]})
                with urllib.request.urlopen(request,timeout=self.config.llm_timeout) as response:
                    result = json.load(response)
                raw = result['choices'][0]['message']['content']
                action = json.loads(raw)
                validate_action(action,sim.data.routes)
                record.update(raw=raw,usage=result.get('usage'),action=action)
                record['attempts'].append(dict(seconds=time.monotonic()-started,ok=True))
            except urllib.error.HTTPError as exc:
                status = exc.code
                record['attempts'].append(dict(seconds=time.monotonic()-started,
                                               error='HTTPError',status=status))
                if status in (400,401,403,404):
                    raise RuntimeError(f'LLM provider returned HTTP {status}. Check the endpoint, model, and API access.') from None
                if attempt < 2:
                    retry_after = exc.headers.get('Retry-After','') if exc.headers else ''
                    delay = max(65 if status==429 else 2**attempt,
                                int(retry_after) if retry_after.isdecimal() else 0)
                    if delay > 120:
                        raise RuntimeError(f'LLM provider returned HTTP {status}. Retry in at least {delay} seconds.') from None
                    time.sleep(delay)
            except Exception as exc:
                record['attempts'].append(dict(seconds=time.monotonic()-started,error=type(exc).__name__))
                if attempt<2:
                    time.sleep(2**attempt)
            else:
                if self.call_log is not None:
                    save_json(self.call_log,self.calls)
                return action
        last = record['attempts'][-1]
        if last.get('status') == 429:
            raise RuntimeError('Gemini returned HTTP 429 after retries. Check project rate and daily quota, then resume.')
        reason = f"HTTP {last['status']}" if 'status' in last else last['error']
        raise RuntimeError(f'LLM decision failed after three attempts ({reason}). Resume to retry.')


def load_setup():
    config = Config.load('config.json')
    frozen = json.loads(Path(config.frozen_calibration).read_text(encoding='utf-8'))
    if not frozen.get('frozen'):
        raise ValueError('Freeze calibration first')
    runtime = {'arm','day','seed','frozen_calibration','research_settings_confirmed','examples','history_minutes','decision_interval'}
    for key,value in frozen['config'].items():
        if key not in runtime and not key.startswith('llm_') and getattr(config,key)!=value:
            raise ValueError(f'Frozen calibration mismatch: {key}')
    config = replace(config,decision_interval=15,history_minutes=60)
    data = Dataset(config)
    if data.digest!=frozen['workbook_sha256']:
        raise ValueError('Workbook differs from frozen calibration')
    return config,data,frozen


def run_day(data,config,folder,examples=(),checkpoint=None,legacy_endpoint=None):
    sim = ExperimentSimulation(data,config)
    if config.arm=='BASE1':
        sim.controller = NetworkRule()
    elif config.arm.startswith('LLM'):
        sim.controller = ExperimentLLM(config,examples,folder/'llm_calls.json',legacy_endpoint)
    boundaries = sorted({config.start,config.end,*[t for _,a,b in PERIODS for t in (a,b)]})
    samples = {}
    saved = None
    if checkpoint is not None:
        boundaries = sorted(set(boundaries+[checkpoint]))
    try:
        for t in boundaries:
            before_events(sim,t)
            samples[t] = totals(sim)
            if t==checkpoint:
                before_decision(sim,t)
                saved = sim.clone()
                saved.controller = copy.deepcopy(sim.controller)
            save_json(ROOT/'progress.json',dict(status='running',day=config.day,arm=config.arm,seed=config.seed,time=t))
        rows = [dict(day=config.day,period=name,seed=config.seed,strategy=config.arm,
                     **difference(samples[a],samples[b],config.capacity)) for name,a,b in PERIODS]
        rows.append(dict(day=config.day,period='Full day',seed=config.seed,strategy=config.arm,
                         **difference(samples[config.start],samples[config.end],config.capacity)))
        save_json(folder/'rows.json',rows)
        save_json(folder/'dispatches.json',sim.dispatch_log)
        save_json(folder/'manifest.json',dict(config=asdict(config),workbook_sha256=data.digest,complete=True,
            examples_sha256=hashlib.sha256(json.dumps(examples,sort_keys=True).encode()).hexdigest()))
        return saved,rows
    finally:
        if hasattr(sim.controller,'calls'):
            controller = sim.controller
            remaining = controller.saved_calls[len(controller.calls):]
            save_json(folder/'llm_calls.json',controller.calls+remaining)


def generate_examples(config,data):
    draft = ROOT/'examples_draft.json'
    saved = json.loads(draft.read_text(encoding='utf-8')) if draft.exists() else {}
    cases = saved['cases'] if saved.get('checkpoint_source')=='BASE0' else []
    for index,(day,t) in enumerate(TRAINING):
        if index<len(cases): continue
        scenario = replace(config,day=day,seed=201,arm='BASE0')
        state = ExperimentSimulation(data,scenario)
        save_json(ROOT/'progress.json',dict(status='running',task='examples',day=day,time=t))
        before_decision(state,t)
        outcomes = []
        for policy in ('BASE0','BASE1'):
            branch = state.clone()
            branch.controller = NetworkRule() if policy=='BASE1' else None
            before = totals(branch)
            first_decision = len(branch.decision_log)
            before_events(branch,t+60)
            decisions = [
                dict(time=entry['time'],action=entry['action'])
                for entry in branch.decision_log[first_decision:]
            ] if policy=='BASE1' else [
                dict(time=minute,action=dict(routes=[])) for minute in range(t,t+60,15)
            ]
            metrics = difference(before,totals(branch),config.capacity)
            outcomes.append(dict(policy=policy,horizon_minutes=60,decisions=decisions,
                                 metrics_60m=metrics))
        preferred = min(range(len(outcomes)),key=lambda i:(outcomes[i]['metrics_60m']['total_wait'],
            outcomes[i]['metrics_60m']['denied_attempts'],outcomes[i]['metrics_60m']['extra_bus_hours'],i))
        cases.append(dict(case_id=f'training-{index+1}',day=day,seed=201,time=t,snapshot=state.observation(),
            history=history(state),dispatch_events=[e for e in state.dispatch_log if t-60<=e['time']<t],
            candidate_outcomes=outcomes,preferred_action_index=preferred,horizon_minutes=60))
        save_json(draft,dict(cases=cases,frozen=False,checkpoint_source='BASE0'))
    document = dict(cases=cases,frozen=True,selection='wait_then_denied_attempts_then_service_hours',
                    checkpoint_source='BASE0',training_seed=201,comparison='BASE0_vs_BASE1')
    document['version'] = hashlib.sha256(json.dumps(document,sort_keys=True).encode()).hexdigest()
    save_json(ROOT/'examples.json',document)


def export_tables():
    rows = []
    for path in sorted((ROOT/'runs').glob('*/rows.json')):
        if not (path.parent/'manifest.json').exists(): continue
        rows.extend(row for row in json.loads(path.read_text(encoding='utf-8'))
                    if row['day'] in TEST_DAYS and row['seed'] in TEST_SEEDS)
    save_json(ROOT/'results.json',rows)
    if not rows: return
    with (ROOT/'results.csv').open('w',newline='',encoding='utf-8') as file:
        writer = csv.DictWriter(file,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    groups = {}
    for row in rows:
        groups.setdefault((row['day'],row['period'],row['strategy']),[]).append(row)
    summary = []
    for (day,period,arm),group in groups.items():
        values = {key:sum(r[key] for r in group)/len(group) if all(r[key] is not None for r in group) else None
                  for key in rows[0] if key not in ('day','period','strategy','seed')}
        summary.append(dict(day=day,period=period,strategy=arm,seeds=[r['seed'] for r in group],**values))
    save_json(ROOT/'summary.json',summary)
    with (ROOT/'summary.csv').open('w',newline='',encoding='utf-8') as file:
        writer = csv.DictWriter(file,fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('task',choices=['examples','baselines','evaluate'])
    args = parser.parse_args()
    ROOT.mkdir(parents=True,exist_ok=True)
    config,data,frozen = load_setup()
    examples = []
    if args.task=='evaluate':
        if not config.llm_endpoint or not config.llm_model or not os.environ.get(config.llm_key_env):
            raise ValueError('Set provider endpoint, model and API key environment variable')
        doc = json.loads((ROOT/'examples.json').read_text(encoding='utf-8'))
        if (not doc.get('frozen') or doc.get('comparison')!='BASE0_vs_BASE1'
                or doc.get('checkpoint_source')!='BASE0' or len(doc['cases'])!=4):
            raise ValueError('Four frozen examples from BASE0 checkpoints required')
        examples = doc['cases']
        if any(c['day'] in TEST_DAYS for c in examples): raise ValueError('Example/test overlap')
    manifest = dict(calibration=frozen,config=asdict(config),test_days=TEST_DAYS,seeds=TEST_SEEDS,
                    training=TRAINING,periods=PERIODS,protocol_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    manifest['source_sha256'] = {name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                for name in ('simulation.py','data.py','config.py','controllers.py')}
    path = ROOT/'manifest.json'
    legacy_endpoint = None
    if path.exists():
        previous = json.loads(path.read_text(encoding='utf-8'))
        for key in ('calibration','source_sha256'):
            if previous[key]!=manifest[key]: raise ValueError('Experiment provenance changed; archive previous output first')
        # Request handling and evaluation scope changed without changing retained scenarios.
        prior_hash = previous['protocol_sha256']
        if prior_hash not in (manifest['protocol_sha256'],
                              '707ea32f3a4f0c76b849781f18338ac888b31fcdd26180509d9ea4c49595c1ec',
                              'e2e28146f00890cbb83e286c01a16be034799b1e89c50e504e18cc19e6fe7075',
                              'ace80c3c04ddb31ba08f0cb6a956a1ffba753bef144e49220f79e1a701767b8e',
                              '589cf2ed973b19ffdaa25ec31cb31bd866d5eae038d5ec6c7ffe9e98d5780935'):
            raise ValueError('Experiment provenance changed; archive previous output first')
        legacy_endpoint = previous.get('legacy_llm_endpoint', previous['config']['llm_endpoint'])
        manifest['legacy_llm_endpoint'] = legacy_endpoint
    save_json(path,manifest)
    try:
        if args.task=='examples':
            if (ROOT/'examples.json').exists(): raise ValueError('Examples already frozen')
            generate_examples(config,data)
        else:
            arms = ARMS if args.task=='evaluate' else ARMS[:2]
            for day in TEST_DAYS:
                for seed in TEST_SEEDS:
                    for arm in arms:
                        folder = ROOT/'runs'/f'{day}_{seed}_{arm}'
                        scenario = replace(config,day=day,seed=seed,arm=arm)
                        if (folder/'manifest.json').exists():
                            existing = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
                            expected = asdict(scenario)
                            prior = existing['config'].copy()
                            expected.pop('llm_timeout',None)
                            prior.pop('llm_timeout',None)
                            if not arm.startswith('LLM'):
                                expected = {k:v for k,v in expected.items() if not k.startswith('llm_')}
                                prior = {k:v for k,v in prior.items() if not k.startswith('llm_')}
                            if prior!=expected: raise ValueError('Completed run uses different settings')
                            if arm.startswith('LLM') and existing['examples_sha256']!=hashlib.sha256(json.dumps(examples,sort_keys=True).encode()).hexdigest():
                                raise ValueError('Completed run uses different examples')
                            continue
                        run_day(data,scenario,folder,examples,legacy_endpoint=legacy_endpoint)
                        export_tables()
            export_tables()
        save_json(ROOT/'progress.json',dict(status='complete',task=args.task))
    except Exception as exc:
        save_json(ROOT/'progress.json',dict(status='failed',task=args.task,error=str(exc)))
        raise


if __name__=='__main__':
    import msvcrt
    ROOT.mkdir(parents=True,exist_ok=True)
    with (ROOT/'job.lock').open('a+b') as lock:
        if lock.tell()==0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        try:
            main()
        except Exception as exc:
            save_json(ROOT/'progress.json',dict(status='failed',error=str(exc)))
            raise
