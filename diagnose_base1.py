"""Read-only replay of the original BASE1 policy, with boarding diagnostics."""
import json
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from nextbus.protocol import load_setup,ExperimentSimulation,NetworkRule,before_events,before_decision,totals,difference
from nextbus.experiments import save_json

cfg,data,_=load_setup()
cfg=replace(cfg,day='2026-08-24',seed=101,arm='BASE0')
sim=ExperimentSimulation(data,cfg)
report=[]
for name,start,end in [('Morning',420,570),('Evening',1020,1170)]:
    before_decision(sim,start)
    branch=sim.clone(); branch.controller=NetworkRule()
    opening=totals(branch)
    before_events(branch,end)
    events=[e for e in branch.boarding_events if start<=e['time']<end]
    by_stop=defaultdict(lambda:dict(attempts=0,denied=0))
    for e in events:
        row=by_stop[(e['route'],e['stop_name'])]
        row['attempts']+=e['attempts'];row['denied']+=e['denied']
    report.append(dict(period=name,metrics=difference(opening,totals(branch),cfg.capacity),
                       stops=sorted([dict(route=r,stop=s,**v) for (r,s),v in by_stop.items()],key=lambda x:-x['denied']),
                       audit=branch.rule_audit,denial_events=[e for e in events if e['denied']]))
    save_json(Path('outputs/base1_diagnosis.json'),report)
    print(name,report[-1]['metrics'],flush=True)
