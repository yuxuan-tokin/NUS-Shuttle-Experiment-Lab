import json
import os
from pathlib import Path
import time
import urllib.request

NO_ACTION = dict(dispatch=False,route_id=None,quantity=0)


def recent(sim):
    return [s for s in sim.history if sim.t-sim.config.history_minutes<=s['time']<sim.t]


class RuleController:
    def __init__(self):
        self.last = {}
        self.details = []

    def decide(self,sim):
        runs = {r:{} for r in sim.data.routes}
        for snapshot in recent(sim):
            for vehicle in snapshot['vehicles']:
                route = runs[vehicle['route_id']]
                route[vehicle['run_id']] = route.get(vehicle['run_id'],False) or vehicle['crowd_level']=='HIGH'
        pending = {x['route_id'] for x in sim.observation()['extra_bus_commitments'] if x['status']=='en_route_from_depot'}
        eligible = []
        self.details = []
        for route,values in runs.items():
            total,crowded = len(values),sum(values.values())
            share = crowded/total if total else None
            self.details.append(dict(route=route,crowded=crowded,total=total,share=share,pending=route in pending))
            if share is not None and crowded>=sim.config.rule_min_crowded and share>=sim.config.rule_share and route not in pending and sim.t-self.last.get(route,-1e9)>=sim.config.rule_cooldown:
                eligible.append((-share,route))
        if not eligible: return NO_ACTION.copy()
        route = min(eligible)[1]
        self.last[route] = sim.t
        return dict(dispatch=True,route_id=route,quantity=1)


class LLMController:
    def __init__(self,config):
        self.config = config
        self.calls = []
        self.examples = []
        if config.arm in ('LLM-C','LLM-D'):
            document = json.loads(Path(config.examples).read_text(encoding='utf-8'))
            if not document.get('frozen') or len(document.get('cases',[]))!=4:
                raise ValueError('Few-shot arms require exactly four frozen cases')
            self.examples = document['cases']

    def input(self,sim):
        with_history = self.config.arm in ('LLM-B','LLM-D')
        payload = dict(network={r:seq for r,seq in sim.data.routes.items()},stops=[dict(id=i,latitude=p.xy[0],longitude=p.xy[1]) for i,p in enumerate(sim.data.places[:sim.data.stop_count])],depots=sim.data.depots,current=sim.observation(),objective=dict(weights=sim.config.objective_weights,scales=sim.config.objective_scales),depot_speed_kmh=sim.config.depot_speed_kmh,road_multiplier=sim.config.road_multiplier)
        if with_history:
            payload['history'] = recent(sim)
            payload['dispatch_events'] = [e for e in sim.dispatch_log if sim.t-30<=e['time']<sim.t]
        if self.examples:
            payload['examples'] = []
            for case in self.examples:
                allowed = ['case_id','snapshot','candidate_outcomes','preferred_action_index','horizon_minutes']
                if with_history: allowed += ['history','dispatch_events']
                payload['examples'].append({k:case[k] for k in allowed if k in case})
        return payload

    def decide(self,sim):
        payload = self.input(sim)
        instructions = ('Choose no dispatch or one extra bus on one route over the next 30 minutes. '
            'Balance all five metrics: total waiting passenger-minutes, capacity failure rate, overall fleet utilization, extra buses dispatched, and extra passenger-service bus-hours. '
            'No metric has fixed priority. Use your judgment without a prescribed weighting formula or tie-breaking hierarchy. '
            'Treat utilization as an efficiency signal, not a target to maximize at the expense of denied boardings. '
            'Historical preferred actions reflect an earlier objective; compare their measured outcomes using this balanced objective. '
            'Existing buses continue. Extra buses travel from their depot, complete one ordered route and return. '
            'Crowd levels are LOW through 25 percent capacity, MEDIUM above 25 and below 75, HIGH from 75. '
            'Return only JSON with dispatch (boolean), route_id (route identifier or null), quantity (0 or 1). '
            'Historical preferred_action_index applies to its listed candidates. Times and durations are minutes, coordinates are latitude and longitude.')
        body = dict(model=self.config.llm_model,temperature=self.config.llm_temperature,messages=[dict(role='system',content=instructions),dict(role='user',content=json.dumps(payload))],response_format=dict(type='json_object'))
        record = dict(time=sim.t,input=payload,model=self.config.llm_model,request=body)
        start = time.monotonic()
        action = NO_ACTION.copy()
        try:
            if not self.config.llm_endpoint or not self.config.llm_model:
                raise ValueError('Set llm_endpoint and llm_model')
            key = os.environ.get(self.config.llm_key_env)
            if not key: raise ValueError(f'Set environment variable {self.config.llm_key_env}')
            request = urllib.request.Request(self.config.llm_endpoint,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':f'Bearer {key}'})
            with urllib.request.urlopen(request,timeout=self.config.llm_timeout) as response:
                result = json.load(response)
            record['usage'] = result.get('usage')
            record['raw'] = result['choices'][0]['message']['content']
            action = json.loads(record['raw'])
        except Exception as exc:
            # Do not log server response bodies or authorization headers.
            record['error'] = type(exc).__name__
        record['latency_seconds'] = time.monotonic()-start
        record['parsed_action'] = action
        self.calls.append(record)
        return action


def controller(config):
    if config.arm=='BASE0': return None
    if config.arm=='BASE1': return RuleController()
    return LLMController(config)
