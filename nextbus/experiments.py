from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import platform
import time
import numpy as np
from .config import Config
from .controllers import controller, NO_ACTION, recent
from .simulation import Simulation


def save_json(path,value):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2,allow_nan=False),encoding='utf-8')
    for attempt in range(5):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def run(data,config,output):
    sim = Simulation(data,config)
    sim.controller = controller(config)
    sim.advance(config.end)
    target = Path(output)
    target.mkdir(parents=True,exist_ok=True)
    save_json(target/'metrics.json',sim.metrics())
    save_json(target/'timeseries.json',sim.series)
    save_json(target/'attendance.json',sim.attendance)
    save_json(target/'decisions.json',sim.decision_log)
    save_json(target/'dispatches.json',sim.dispatch_log)
    if hasattr(sim.controller,'calls'): save_json(target/'llm_calls.json',sim.controller.calls)
    save_json(target/'manifest.json',dict(config=asdict(config),workbook_sha256=data.digest,python=platform.python_version(),excluded_sessions=sim.excluded,warnings=data.warnings,schema_version=1))
    return sim


def objective(metrics,config):
    if not config.objective_weights:
        raise ValueError('Explicit objective_weights and positive objective_scales are required for preferred-action labels')
    value = 0.
    for name,weight in config.objective_weights.items():
        scale = config.objective_scales.get(name,0)
        if weight<0 or scale<=0: raise ValueError('Objective weights must be nonnegative and scales positive')
        metric = metrics.get(name)
        if metric is None:
            # No eligible boarding opportunity means zero capacity-denial burden.
            if name=='capacity_failure_rate': metric = 0.
            else: raise ValueError(f'Undefined objective metric {name}')
        value += weight*metric/scale
    return value


def counterfactual(sim,route):
    if route not in sim.data.routes: raise ValueError('Unknown route')
    start,end = sim.t,sim.t+30
    outcomes = []
    for action in [NO_ACTION.copy(),dict(dispatch=True,route_id=route,quantity=1)]:
        branch = sim.clone()
        before_wait = branch.wait_total
        branch.eligible_people,branch.failed_people = set(),set()
        latest = len(branch.buses)
        branch.execute(action)
        branch.advance(end)
        metrics = dict(waiting_passenger_minutes=branch.wait_total-before_wait,capacity_failure_rate=len(branch.failed_people)/len(branch.eligible_people) if branch.eligible_people else None,extra_bus_hours=0.,extra_dispatch_count=int(action['dispatch']),extra_bus_utilization=None)
        if action['dispatch']:
            bus = branch.buses[latest]
            metrics['extra_bus_hours'] = (min(end,bus['finish'] if bus['finish'] is not None else end)-start)/60
            service,passengers,_ = branch.bus_metrics(bus)
            metrics['extra_bus_utilization'] = passengers/(branch.config.capacity*service) if service else None
        outcomes.append(dict(action=action,metrics_30m=metrics,objective_score=objective(metrics,sim.config),remaining_commitments=branch.observation()['extra_bus_commitments']))
    scores = [x['objective_score'] for x in outcomes]
    return dict(case_id=f'{sim.config.day}-{sim.config.seed}-{start:g}-{route}',day=sim.config.day,seed=sim.config.seed,time=start,snapshot=sim.observation(),history=recent(sim),dispatch_events=[x for x in sim.dispatch_log if start-30<=x['time']<start],candidate_outcomes=outcomes,preferred_action_index=int(np.argmin(scores)),horizon_minutes=30,rollout_policy='no_new_dispatch',workbook_sha256=sim.data.digest,config=asdict(sim.config))


def representative_days(data):
    chosen = []
    for semester in (1,2):
        dates = [day for day,term in data.teaching_dates().items()
                 if term[0]==semester and data.sessions(day)[0]]
        dates.sort(key=lambda day:sum(s.target*(s.end-s.start) for s in data.sessions(day)[0]))
        if dates:
            chosen.extend(dates[round((len(dates)-1)*quantile)] for quantile in (.25,.75))
    return sorted(set(chosen))


def distribution(values):
    if not len(values):
        return None
    p10,median,p90 = np.percentile(values,[10,50,90])
    return dict(count=len(values),mean=float(np.mean(values)),std=float(np.std(values)),
                p10=float(p10),median=float(median),p90=float(p90),zero_share=float(np.mean(values==0)))


def mean_complete(values):
    return float(np.mean(values)) if values and all(value is not None for value in values) else None


def ratio(count,total):
    return float(count/total) if total else None


def calibration_day(sim, building_count, plan_homes):
    error, squared, target, samples = 0.,0.,0.,0.
    for row,later in zip(sim.attendance,sim.attendance[1:]):
        dt = later['time']-row['time']
        for actual,expected in row['buildings'].values():
            error += dt*abs(actual-expected)
            squared += dt*(actual-expected)**2
            target += dt*expected
        samples += dt*building_count
    residents = sim.home>=0
    with_plan = residents & np.isin(sim.home,plan_homes)
    no_plan = residents & ~with_plan
    campus_lunch = (sim.campus_meals_by_person & 2)!=0
    campus_dinner = (sim.campus_meals_by_person & 4)!=0
    dining_plan = distribution(sim.dining_minutes_by_person[with_plan])
    dining_no_plan = distribution(sim.dining_minutes_by_person[no_plan])
    return dict(day=sim.config.day,seed=sim.config.seed,attendance_absolute_error=error,
                attendance_target_minutes=target,attendance_wape=ratio(error,target),
                rmse=float(np.sqrt(squared/samples)) if samples else None,
                off_return=sim.exit_share_at_check,on_return=sim.on_campus_return_share_at_check,
                lunch=ratio(np.count_nonzero(campus_lunch & residents),residents.sum()),
                dinner=ratio(np.count_nonzero(campus_dinner & residents),residents.sum()),
                dining_plan=dining_plan['mean'] if dining_plan else None,
                dining_no_plan=dining_no_plan['mean'] if dining_no_plan else None,
                trips=sim.completed_trips,shuttle_trips=sim.completed_shuttle_trips,
                long_feasible_trips=sim.completed_long_feasible,long_shuttle_trips=sim.completed_long_shuttle,
                trips_2km=sim.completed_2km,feasible_2km_trips=sim.completed_2km_feasible,
                shuttle_2km_trips=sim.completed_2km_shuttle,
                long_trips_shuttle_competitive_without_wait=sim.completed_long_competitive,
                student_distributions=dict(dining_plan_minutes=dining_plan,dining_no_plan_minutes=dining_no_plan,
                                           class_minutes=distribution(sim.class_minutes_by_person)))


def calibration_checks(rows,config):
    by_day = {}
    for row in rows:
        by_day.setdefault(row['day'],[]).append(row)
    checks = {}
    def check(name,value,lower,upper,scale):
        miss = None if value is None else max(
            0.,lower-value if lower is not None else 0.,value-upper if upper is not None else 0.)
        checks[name] = dict(value=value,minimum=lower,maximum=upper,passes=miss is not None and miss<=1e-12,
                            miss=miss,normalized_miss=miss/scale if miss is not None else None)
    for day,records in by_day.items():
        check(f'off_return:{day}',mean_complete([row['off_return'] for row in records]),config.exit_target,None,.05)
        check(f'on_return:{day}',mean_complete([row['on_return'] for row in records]),config.on_campus_return_target,None,.10)
    for key,minimum,scale in [('lunch',.60,.05),('dinner',.60,.05),
                              ('dining_plan',60,15),('dining_no_plan',40,10)]:
        check(key,mean_complete([row[key] for row in rows]),minimum,None,scale)
    check('attendance_wape',ratio(sum(row['attendance_absolute_error'] for row in rows),
                                 sum(row['attendance_target_minutes'] for row in rows)),None,.20,.20)
    all_share = ratio(sum(row['shuttle_trips'] for row in rows),sum(row['trips'] for row in rows))
    long_share = ratio(sum(row['long_shuttle_trips'] for row in rows),sum(row['long_feasible_trips'] for row in rows))
    shuttle_2km = sum(row.get('shuttle_2km_trips',0) for row in rows)
    shares = dict(all=all_share,long_feasible=long_share,
                  all_2km=ratio(shuttle_2km,sum(row.get('trips_2km',0) for row in rows)),
                  long_feasible_2km=ratio(shuttle_2km,sum(row.get('feasible_2km_trips',0) for row in rows)))
    check('shuttle_share',shares[config.shuttle_calibration_scope],.50,None,.50)
    return checks,shares


def calibrate(data,config,candidates,days,seeds,output,screen=False,diagnostics=False):
    valid = data.days()
    days,seeds = sorted(set(days)),list(dict.fromkeys(seeds))
    if not days or not set(days)<=set(valid) or (not screen and days!=valid):
        raise ValueError('Final calibration requires all valid instructional dates')
    if not candidates or not seeds or (not screen and len(seeds)<2):
        raise ValueError('Provide candidates and at least two distinct seeds for final calibration')
    if config.population!=25000 or config.observation_interval!=1 or config.start!=360 or config.end!=1440:
        raise ValueError('Calibration requires 25,000 students and minute samples for the full 06:00–24:00 day')
    if config.exit_check!=1410:
        raise ValueError('Calibration uses the 23:30 return check')
    allowed = {'class_base_score','class_alpha','class_distance','class_size_eta','class_lead_minutes',
               'class_post_start_peak_minutes','class_long_cutoff_minutes','class_destination_multipliers',
               'home_off_boost','home_on_boost','study_total_weight','study_continuation_share',
               'dining_base_score','meal_score_adjustments','meal_peak_strength','meal_urgency','meal_plan_bonus',
               'walk_multiplier'}
    buildings = {p.building for p in data.places if p.kind=='venue'}
    plan_homes = sorted({data.places[i].home for i in data.activities
                         if data.places[i].kind=='dining' and data.places[i].home>=0 and any(data.places[i].meals)})
    result = dict(schema_version=2,stage='screen' if screen else 'full',frozen=False,chosen=None,
                  candidates=[],workbook_sha256=data.digest,config=asdict(config),days=days,seeds=seeds)
    for index,params in enumerate(candidates,1):
        if not isinstance(params,dict) or not set(params)<=allowed:
            raise ValueError('Unsupported calibration parameter')
        replace(config,**params).validate()
        record = dict(parameters=params,days=[],student_patterns=[],complete=False)
        result['candidates'].append(record)
        for seed in seeds:
            lunches = np.zeros(config.population,int)
            dinners = np.zeros(config.population,int)
            class_time = np.zeros(config.population)
            for day in days:
                c = replace(config,**params,day=day,seed=seed,arm='BASE0')
                sim = Simulation(data,c)
                for boundary in range(540,1441,180):
                    sim.advance(boundary)
                    print(f'Candidate {index}/{len(candidates)} {day}, seed {seed}: reached {boundary//60:02d}:00',flush=True)
                row = calibration_day(sim,len(buildings),plan_homes)
                if diagnostics:
                    from .attendance_diagnostics import attendance_breakdown
                    row['attendance_breakdown'] = attendance_breakdown(sim)
                record['days'].append(row)
                lunches += (sim.campus_meals_by_person & 2)!=0
                dinners += (sim.campus_meals_by_person & 4)!=0
                class_time += sim.class_minutes_by_person
                save_json(output,result)
                print(f'Candidate {index}/{len(candidates)} completed {day}, seed {seed}',flush=True)
            residents = sim.home>=0
            record['student_patterns'].append(dict(
                seed=seed,lunch_days=distribution(lunches[residents]),dinner_days=distribution(dinners[residents]),
                mean_daily_class_minutes=distribution(class_time/len(days))))
        checks,shuttle_shares = calibration_checks(record['days'],config)
        record.update(complete=True,checks=checks,shuttle_shares=shuttle_shares,
                      rmse=mean_complete([row['rmse'] for row in record['days']]),
                      qualifies=all(item['passes'] for item in checks.values()))
        save_json(output,result)
    def rank(record):
        missing = sum(item['normalized_miss'] is None for item in record['checks'].values())
        largest = max((item['normalized_miss'] or 0.) for item in record['checks'].values())
        rmse = record['rmse'] if record['rmse'] is not None else float('inf')
        return (0,0,0.,rmse) if record['qualifies'] else (1,missing,largest,rmse)
    result['ranking'] = sorted(range(len(candidates)),key=lambda i:rank(result['candidates'][i]))
    best = result['candidates'][result['ranking'][0]]
    if not screen and best['qualifies']:
        result['frozen'] = True
        result['chosen'] = best['parameters']
    save_json(output,result)
    return result


def freeze_examples(paths,output,test_days):
    cases = [json.loads(Path(p).read_text(encoding='utf-8')) for p in paths]
    if len(cases)!=4 or len({c['case_id'] for c in cases})!=4:
        raise ValueError('Select four distinct cases')
    if any(c['day'] in test_days for c in cases):
        raise ValueError('Example days overlap test days')
    if len({c['workbook_sha256'] for c in cases})!=1:
        raise ValueError('Cases use different datasets')
    payload = dict(frozen=True,cases=cases,test_days=test_days)
    payload['version'] = hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    save_json(output,payload)


def compare(data,config,days,seeds,arms,output,formal=True):
    if formal:
        if not config.research_settings_confirmed or not config.frozen_calibration:
            raise ValueError('Formal comparison requires confirmed settings and frozen calibration')
        fitted = json.loads(Path(config.frozen_calibration).read_text(encoding='utf-8'))
        if fitted.get('schema_version')!=2 or not fitted['frozen'] or fitted['workbook_sha256']!=data.digest:
            raise ValueError('Calibration is unfrozen or uses another workbook')
        if fitted['days']!=data.days():
            raise ValueError('The teaching calendar differs from the frozen calibration')
        experiment_settings = {'day','seed','arm','workbook','frozen_calibration','research_settings_confirmed',
                               'examples','objective_weights','objective_scales','history_minutes',
                               'decision_interval','rule_share','rule_min_crowded','rule_cooldown'}
        frozen_settings = fitted['config'] | fitted['chosen']
        for key,value in frozen_settings.items():
            if key not in experiment_settings and not key.startswith('llm_') and getattr(config,key)!=value:
                raise ValueError(f'Apply frozen calibration value for {key}')
        if any(a in ('LLM-C','LLM-D') for a in arms):
            examples = json.loads(Path(config.examples).read_text(encoding='utf-8'))
            if any(c['day'] in days for c in examples['cases']): raise ValueError('Example/test overlap')
    results = []
    for day in days:
        for seed in seeds:
            for arm in arms:
                c = replace(config,day=day,seed=seed,arm=arm)
                sim = run(data,c,Path(output)/f'{day}_{seed}_{arm}')
                results.append(dict(day=day,seed=seed,arm=arm,metrics=sim.metrics()))
                save_json(Path(output)/'comparison.json',results)
    return results
