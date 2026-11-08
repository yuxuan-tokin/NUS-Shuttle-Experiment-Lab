"""Resumable, fixed-parameter validation across instructional days and seeds."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path

import numpy as np

from .config import Config
from .data import Dataset
from .experiments import calibration_checks, calibration_day, distribution, save_json
from .simulation import Simulation


def initialize_worker(settings):
    global config, data, building_count, plan_homes
    config = Config(**settings)
    data = Dataset(config)
    building_count = len({place.building for place in data.places if place.kind=='venue'})
    plan_homes = sorted({data.places[i].home for i in data.activities
                         if data.places[i].kind=='dining' and data.places[i].home>=0 and any(data.places[i].meals)})


def simulate(job, checkpoint):
    day,seed = job
    scenario = replace(config,day=day,seed=seed,arm='BASE0')
    sim = Simulation(data,scenario).advance(scenario.end)
    row = calibration_day(sim,building_count,plan_homes)
    # One atomic file holds the metrics and the matching per-student outcomes.
    temporary = checkpoint.with_suffix('.tmp')
    with temporary.open('wb') as file:
        np.savez_compressed(file,row=json.dumps(row,allow_nan=False),homes=sim.home,
                            meals=sim.campus_meals_by_person,dining=sim.dining_minutes_by_person,
                            class_time=sim.class_minutes_by_person)
    temporary.replace(checkpoint)
    return row


def summarize(rows, config, expected_jobs, output, patterns=None):
    checks,shuttle = calibration_checks(rows,config) if rows else ({},{})
    def spread(key):
        values = np.array([row[key] for row in rows if row[key] is not None])
        if not len(values):
            return None
        return dict(distribution(values),minimum=float(values.min()),maximum=float(values.max()))
    run_metrics = ('attendance_wape','rmse','lunch','dinner','dining_plan','dining_no_plan',
                   'off_return','on_return')
    runs_by_seed = {}
    for seed in sorted({row['seed'] for row in rows}):
        selected = [row for row in rows if row['seed']==seed]
        seed_checks,seed_shuttle = calibration_checks(selected,config)
        runs_by_seed[str(seed)] = dict(runs=len(selected),checks=seed_checks,shuttle_shares=seed_shuttle,
                                     mean_rmse=float(np.mean([row['rmse'] for row in selected])))
    limits = {'attendance_wape':(None,.20),'lunch':(.60,None),'dinner':(.60,None),
              'dining_plan':(60,None),'dining_no_plan':(40,None),
              'off_return':(config.exit_target,None),'on_return':(config.on_campus_return_target,None)}
    run_pass_counts = {}
    for key,(low,high) in limits.items():
        run_pass_counts[key] = sum(row[key] is not None and
                                   (low is None or row[key]>=low) and (high is None or row[key]<=high)
                                   for row in rows)
    shuttle_keys = dict(all=('shuttle_trips','trips'),all_2km=('shuttle_2km_trips','trips_2km'),
                        long_feasible=('long_shuttle_trips','long_feasible_trips'),
                        long_feasible_2km=('shuttle_2km_trips','feasible_2km_trips'))
    numerator,denominator = shuttle_keys[config.shuttle_calibration_scope]
    run_pass_counts['shuttle_share'] = sum(row[denominator]>0 and row[numerator]/row[denominator]>=.50
                                          for row in rows)
    save_json(output/'summary.json',dict(
        complete=len(rows)==expected_jobs and patterns is not None,completed_runs=len(rows),expected_runs=expected_jobs,
        checks=checks,shuttle_shares=shuttle,run_pass_counts=run_pass_counts,
        distributions={key:spread(key) for key in run_metrics},
        per_seed=runs_by_seed,student_patterns=patterns,days=rows))


def aggregate_students(output, jobs, plan_homes):
    patterns = []
    for seed in sorted({seed for _,seed in jobs}):
        homes = lunches = dinners = dining = class_time = None
        dates = [day for day,job_seed in jobs if job_seed==seed]
        for day in dates:
            with np.load(output/'runs'/f'{day}_seed{seed}.npz',allow_pickle=False) as run:
                if homes is None:
                    homes = run['homes']
                    lunches = np.zeros(len(homes),int)
                    dinners = np.zeros(len(homes),int)
                    dining = np.zeros(len(homes))
                    class_time = np.zeros(len(homes))
                elif not np.array_equal(homes,run['homes']):
                    raise ValueError('Student home assignments changed between checkpoints')
                meals = run['meals']
                lunches += (meals & 2)!=0
                dinners += (meals & 4)!=0
                dining += run['dining']
                class_time += run['class_time']
        residents = homes>=0
        has_plan = residents & np.isin(homes,plan_homes)
        patterns.append(dict(seed=seed,days=len(dates),lunch_days=distribution(lunches[residents]),
                             dinner_days=distribution(dinners[residents]),
                             mean_daily_dining_plan=distribution(dining[has_plan]/len(dates)),
                             mean_daily_dining_no_plan=distribution(dining[residents & ~has_plan]/len(dates)),
                             mean_daily_class_minutes=distribution(class_time/len(dates))))
    return patterns


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    parser.add_argument('--candidates',required=True,help='JSON file containing exactly one approved parameter set')
    parser.add_argument('--output',required=True)
    parser.add_argument('--seeds',default='1,2,3')
    parser.add_argument('--workers',type=int,default=4)
    args = parser.parse_args()
    candidates = json.loads(Path(args.candidates).read_text(encoding='utf-8-sig'))
    if not isinstance(candidates,list) or len(candidates)!=1 or not isinstance(candidates[0],dict):
        raise ValueError('Stability validation requires exactly one parameter set')
    settings = asdict(Config.load(args.config)) | candidates[0]
    settings['arm'] = 'BASE0'
    config = Config(**settings).validate()
    seeds = list(dict.fromkeys(int(value) for value in args.seeds.split(',')))
    if len(seeds)<2 or args.workers<1:
        raise ValueError('Provide at least two distinct seeds and one or more workers')
    if (config.population,config.start,config.end,config.observation_interval,config.exit_check)!=(25000,360,1440,1,1410):
        raise ValueError('Validation requires 25,000 students, an 06:00–24:00 day, minute samples and a 23:30 return check')
    data = Dataset(config)
    days = data.days()
    jobs = [(day,seed) for day in days for seed in seeds]
    output = Path(args.output)
    checkpoints = output/'runs'
    checkpoints.mkdir(parents=True,exist_ok=True)
    sources = ('config.py','data.py','simulation.py','experiments.py','stability.py')
    source_hashes = {name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in sources}
    manifest = dict(config=settings,workbook_sha256=data.digest,source_sha256=source_hashes,days=days,seeds=seeds)
    manifest_path = output/'manifest.json'
    if manifest_path.exists():
        if json.loads(manifest_path.read_text(encoding='utf-8'))!=manifest:
            raise ValueError('Existing checkpoints have a different model, data or seeds; select another output folder')
    else:
        save_json(manifest_path,manifest)
    finished = {}
    remaining = {}
    for job in jobs:
        day,seed = job
        path = checkpoints/f'{day}_seed{seed}.npz'
        if path.exists():
            with np.load(path,allow_pickle=False) as checkpoint:
                finished[job] = json.loads(str(checkpoint['row']))
        else:
            remaining[job] = path
    print(f'{len(finished)}/{len(jobs)} runs complete; {len(remaining)} pending; {args.workers} workers',flush=True)
    summarize([finished[job] for job in jobs if job in finished],config,len(jobs),output)
    if remaining:
        with ProcessPoolExecutor(max_workers=args.workers,initializer=initialize_worker,initargs=(settings,)) as pool:
            futures = {pool.submit(simulate,job,path):job for job,path in remaining.items()}
            for future in as_completed(futures):
                job = futures[future]
                finished[job] = future.result()
                summarize([finished[key] for key in jobs if key in finished],config,len(jobs),output)
                print(f'{len(finished)}/{len(jobs)} completed: {job[0]} seed {job[1]}',flush=True)
    plan_homes = sorted({data.places[i].home for i in data.activities
                         if data.places[i].kind=='dining' and data.places[i].home>=0 and any(data.places[i].meals)})
    patterns = aggregate_students(output,jobs,plan_homes)
    summarize([finished[job] for job in jobs],config,len(jobs),output,patterns)
    print('Validation complete',flush=True)


if __name__=='__main__':
    main()
