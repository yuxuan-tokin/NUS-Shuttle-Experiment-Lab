from dataclasses import replace
import json
import tempfile
import unittest
from pathlib import Path
import numpy as np
from nextbus.config import Config
from nextbus.data import Dataset, Session, departures
from nextbus.simulation import Simulation, class_profile, redistribute_class_scores, study_factor, QUEUE, BUS, OUTSIDE, ENGAGED, FREE, WALK
from nextbus.controllers import RuleController, LLMController
from nextbus.experiments import calibration_checks, counterfactual


class SimulatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = Config(population=120,start=600,end=750)
        cls.data = Dataset(cls.config)

    def test_schedule_anchors_and_uniqueness(self):
        for route in self.data.routes:
            times = departures(route)
            self.assertEqual(len(times),len(set(times)))
            self.assertEqual(times,sorted(times))
        self.assertEqual(departures('A1')[-1],1380)
        self.assertEqual(departures('K')[-1],1384)

    def test_class_distance_exempt_precincts_and_mrt_gateway_weights(self):
        d = self.data
        self.assertTrue(set(d.homes)<=d.class_distance_exempt)
        self.assertTrue({d.stop_codes[code] for code in ('UTOWN','PGP','PGPR')}<=d.class_distance_exempt)
        self.assertIn(d.buildings['ERC'],d.class_distance_exempt)
        self.assertNotIn(d.buildings['COM3'],d.class_distance_exempt)
        self.assertNotIn(d.stop_codes['KR-MRT'],d.class_distance_exempt)
        for facility in d.facilities:
            if facility['id'] in ('ACT:DIN-UTOWN','ACT:DIN-PGP'):
                self.assertIn(facility['destination'],d.class_distance_exempt)
        weights = dict(zip(d.gateways,d.gateway_weights))
        self.assertEqual(weights[d.stop_codes['KR-MRT']],4.)
        self.assertEqual(weights[d.stop_codes['KR-MRT-OPP']],4.)
        self.assertEqual(weights[d.stop_codes['KRB']],1.)

    def test_class_destination_boost_redistributes_class_weight(self):
        self.assertEqual(self.data.class_destination_zones[self.data.buildings['UTSRC']],'UTOWN')
        self.assertNotEqual(self.data.class_destination_zones[self.data.buildings['BIZ2']],'UTOWN')
        weights = np.exp(redistribute_class_scores([0.,0.],[np.log(1.25),0.],1.))
        self.assertAlmostEqual(weights.sum(),2.)
        self.assertAlmostEqual(weights[0]/weights[1],1.25)

    def test_calibration_dates_use_instructional_weeks_only(self):
        dates = self.data.teaching_dates()
        self.assertNotIn('2026-08-10',dates)
        self.assertNotIn('2026-09-21',dates)
        self.assertNotIn('2027-01-04',dates)
        self.assertEqual(dates['2026-09-28'],(1,7,0))
        self.assertEqual(dates['2027-01-11'],(2,1,0))
        self.assertEqual(len(dates),124)

    def test_calibration_checks_do_not_offset_failed_meals_or_missing_return(self):
        row = dict(day='2026-08-17',off_return=None,on_return=.95,lunch=.85,dinner=.55,
                   dining_plan=105.,dining_no_plan=95.,attendance_absolute_error=19.,
                   attendance_target_minutes=100.,trips=100,shuttle_trips=10,
                   long_feasible_trips=16,long_shuttle_trips=10,
                   trips_2km=6,feasible_2km_trips=4,shuttle_2km_trips=3)
        checks,shares = calibration_checks([row],self.config)
        self.assertTrue(checks['lunch']['passes'])
        self.assertFalse(checks['dinner']['passes'])
        self.assertTrue(checks['dining_plan']['passes'])
        self.assertTrue(checks['dining_no_plan']['passes'])
        self.assertFalse(checks['off_return:2026-08-17']['passes'])
        self.assertTrue(checks['attendance_wape']['passes'])
        self.assertTrue(checks['shuttle_share']['passes'])
        self.assertEqual(shares,dict(all=.10,long_feasible=.625,all_2km=.5,long_feasible_2km=.75))
        checks,_ = calibration_checks([row],replace(self.config,shuttle_calibration_scope='long_feasible_2km'))
        self.assertEqual(checks['shuttle_share']['value'],.75)

    def test_short_class_peak_and_elearning(self):
        session = Session(0,0,600,630,20,1)
        self.assertEqual(class_profile(600,session),1)
        self.assertEqual(class_profile(584.9,session),0)
        self.assertEqual(class_profile(585,session),1)
        self.assertAlmostEqual(class_profile(605,session),2/3)
        self.assertEqual(class_profile(615,session),0)
        self.assertAlmostEqual(class_profile(550,session,75,15),1/3)
        long_session = Session(0,0,600,720,20,1)
        self.assertAlmostEqual(class_profile(620,long_session),.5)
        self.assertEqual(class_profile(640,long_session),0)
        for s in self.data.sessions(self.config.day)[0]:
            self.assertNotIn('hybrid',self.data.places[s.place].name.lower())

    def test_replay_and_conservation(self):
        first = Simulation(self.data,self.config).advance(690)
        second = first.clone()
        first.advance(750)
        second.advance(750)
        self.assertEqual(first.metrics(),second.metrics())
        np.testing.assert_array_equal(first.state,second.state)
        np.testing.assert_array_equal(first.loc,second.loc)
        queued = [i for q in first.queues for i in q]
        self.assertEqual(len(queued),len(set(queued)))
        self.assertEqual(set(queued),set(np.flatnonzero(first.state==QUEUE)))
        onboard = [i for b in first.buses for i in b['passengers']]
        self.assertEqual(set(onboard),set(np.flatnonzero(first.state==BUS)))
        self.assertEqual(len(onboard),len(set(onboard)))
        self.assertTrue(all(len(b['passengers'])<=88 for b in first.buses))
        waits = sum(end-start for _,start,end,_ in first.wait_records)
        waits += sum(first.t-first.queue_since[i] for i in queued)
        self.assertAlmostEqual(waits,first.wait_total,places=6)

    def test_rule_requires_two_crowded_runs(self):
        sim = Simulation(self.data,self.config)
        sim.t = 700
        vehicle = dict(run_id='one',route_id='D1',crowd_level='HIGH')
        sim.history = [dict(time=690,vehicles=[vehicle])]
        rule = RuleController()
        self.assertFalse(rule.decide(sim)['dispatch'])
        sim.history[0]['vehicles'].append(dict(vehicle,run_id='two'))
        self.assertTrue(rule.decide(sim)['dispatch'])
        self.assertFalse(rule.decide(sim)['dispatch'])

    def test_observation_boundary_and_memory(self):
        sim = Simulation(self.data,self.config).advance(690)
        a = LLMController(replace(self.config,arm='LLM-A')).input(sim)
        b = LLMController(replace(self.config,arm='LLM-B')).input(sim)
        self.assertNotIn('history',a)
        self.assertTrue(all(660<=x['time']<690 for x in b['history']))
        for vehicle in a['current']['vehicles']:
            self.assertIn(vehicle['crowd_level'],['LOW','MEDIUM','HIGH'])
            self.assertFalse({'occupancy','capacity','load','passengers','queue'} & vehicle.keys())

    def test_case_charges_only_latest_bus_and_exact_horizon(self):
        c = replace(self.config,objective_weights={'waiting_passenger_minutes':1,'extra_bus_hours':1},objective_scales={'waiting_passenger_minutes':1000,'extra_bus_hours':1})
        sim = Simulation(self.data,c).advance(690)
        sim.execute(dict(dispatch=True,route_id='D1',quantity=1))
        sim.advance(695)
        original = sim.metrics()
        case = counterfactual(sim,'R1')
        self.assertEqual(sim.metrics(),original)
        self.assertEqual(case['horizon_minutes'],30)
        no,yes = case['candidate_outcomes']
        self.assertEqual(no['metrics_30m']['extra_bus_hours'],0)
        self.assertIsNone(no['metrics_30m']['extra_bus_utilization'])
        self.assertLessEqual(yes['metrics_30m']['extra_bus_hours'],.5)
        self.assertEqual(yes['metrics_30m']['extra_dispatch_count'],1)

    def test_invalid_actions_do_not_dispatch(self):
        sim = Simulation(self.data,self.config)
        for action in [None,{},dict(dispatch=True,route_id='P',quantity=1),dict(dispatch=True,route_id='D1',quantity=True),dict(dispatch=True,route_id=['D1'],quantity=1)]:
            self.assertFalse(sim.execute(action))
        self.assertEqual(len(sim.dispatch_log),0)

    def test_building_aggregation_and_stop_exclusion(self):
        biz2 = self.data.buildings['BIZ2']
        biz_rooms = [idx for code,idx in self.data.venues.items() if code.startswith('BIZ2-')]
        self.assertGreater(len(biz_rooms),1)
        self.assertEqual(set(biz_rooms),{biz2})
        self.assertIn(biz2,self.data.study_sites)
        self.assertFalse(set(self.data.study_sites)&set(self.data.stop_codes.values()))

    def test_closed_study_arrival_releases_immediately(self):
        sim = Simulation(self.data,self.config)
        facility = next(i for i in self.data.activities if self.data.places[i].id=='ACT:STUDY-MSL')
        destination = self.data.places[facility].destination
        sim.t = 1081
        sim.goal[0] = ('study',destination,-1)
        sim.arrive(0)
        self.assertEqual(sim.state[0],0)
        self.assertEqual(sim.loc[0],destination)
        self.assertIsNone(sim.goal[0])
        self.assertIn(('study',destination),sim.closed[0])

    def test_closed_dining_releases_meal_reservation(self):
        sim = Simulation(self.data,self.config)
        facility = next(i for i in self.data.activities if self.data.places[i].id=='ACT:DIN-FRONTIER')
        sim.t = 1261
        sim.meals[0] = 2
        sim.pending_meal[0] = 1
        sim.goal[0] = ('dining',self.data.places[facility].destination,facility)
        sim.arrive(0)
        self.assertEqual(sim.state[0],0)
        self.assertEqual(sim.meals[0],0)
        self.assertIn(('dining',facility),sim.closed[0])

    def test_wait_prediction_uses_current_progress_and_nominal_future(self):
        sim = Simulation(self.data,self.config)
        sim.t = 700
        sim.departure_table['D1'] = []
        bid = sim.launch('D1',0,True)
        bus = sim.buses[bid]
        bus.update(index=0,segment_start=700,next_time=710,start=700)
        sim.t = 705
        seq = self.data.routes['D1']
        path = (0,0,'D1',1,2,seq[1],seq[2])
        nominal = self.data.segment_times[seq[0],seq[1]]
        self.assertAlmostEqual(sim.expected_wait(path,705),nominal*.5)

    def test_confirmed_facility_buildings_and_access(self):
        facilities = {p['id']:p for p in self.data.facilities}
        for name in ('TECHNO','FRONTIER','DECK'):
            item = facilities['ACT:DIN-'+name]
            self.assertTrue(item['standalone'])
            self.assertEqual(self.data.places[item['destination']].id,item['id'])
            self.assertTrue(item['candidate_stops'])
        self.assertEqual(facilities['ACT:DIN-TECHNO']['candidate_stops'],['IT','CLB'])
        self.assertEqual(facilities['ACT:DIN-FRONTIER']['candidate_stops'],['LT27','S17'])
        self.assertIn('BIZ2',facilities['ACT:DIN-TERRACE']['candidate_stops'])
        self.assertEqual(facilities['ACT:DIN-DECK']['candidate_stops'],['LT13','AS5'])
        self.assertEqual(facilities['ACT:DIN-TERRACE']['destination'],self.data.buildings['COM3'])
        self.assertEqual(self.data.study_sites[self.data.buildings['COM3']]['tier'],'general')

    def test_faster_opposite_stop_beats_slow_primary_and_walking(self):
        sim = Simulation(self.data,self.config)
        origin = self.data.buildings['COM3']
        destination = next(h for h in self.data.homes if 'PGPR' in self.data.places[h].name)
        primary, opposite = self.data.places[destination].stops[:2]
        boarding = self.data.stop_codes['COM3']
        walk = sim.walk_time(origin,destination)
        slow = (0,walk+5,'D2',0,12,boarding,primary)
        fast = (1,walk-1,'D2',0,2,boarding,opposite)
        sim.loc[0] = origin
        sim.itineraries = lambda *_: [slow,fast]
        sim.expected_wait = lambda *_: 1.
        sim.travel(0,destination)
        self.assertEqual(sim.leg[0],fast)

    def test_study_evening_boundary_and_general_closure(self):
        for tier in ('general','canteen'):
            self.assertEqual(study_factor(1049.9,tier,self.config),1)
            self.assertLess(study_factor(1050,tier,self.config),1)
        for tier in ('library','residence'):
            self.assertGreater(study_factor(1050,tier,self.config),1)
        sim = Simulation(self.data,self.config)
        destination = self.data.buildings['BIZ2']
        sim.goal[0] = ('study',destination,-1)
        sim.t = 1260
        sim.arrive(0)
        self.assertEqual(sim.state[0],FREE)
        sim.goal[1] = ('study',destination,-1)
        sim.t = 1259
        sim.arrive(1)
        self.assertEqual(sim.state[1],ENGAGED)
        self.assertTrue(any(event[0]==1289 and event[3]=='release' and event[4]==1 for event in sim.events))

    def test_mrt_dining_is_separate_from_gateway_and_stays_open(self):
        facility = next(i for i in self.data.activities if self.data.places[i].id=='ACT:DIN-KRMRT')
        destination = self.data.places[facility].destination
        self.assertNotIn(destination,self.data.stop_codes.values())
        sim = Simulation(self.data,self.config)
        sim.t = 1300
        sim.goal[0] = ('dining',destination,facility)
        sim.pending_meal[0] = 2
        sim.arrive(0)
        self.assertEqual(sim.state[0],ENGAGED)
        self.assertTrue(any(event[0]==1350 and event[3]=='release' and event[4]==0 for event in sim.events))
        self.assertAlmostEqual(sim.metrics()['meal_participation']['dinner'],1/self.config.population)
        snapshot = sim.snapshot()
        point = next(p for p in snapshot['people'] if p[0]==0)
        self.assertEqual(point[4],2)
        self.assertEqual(point[5],destination)
        self.assertEqual(snapshot['activity_counts']['dining'],1)

    def test_evening_return_counts_resident_studying_at_home(self):
        sim = Simulation(self.data,self.config)
        home = self.data.homes[0]
        sim.home[:] = -1
        sim.home[:3] = home
        sim.loc[:3] = [home,home,self.data.stop_codes['UTOWN']]
        sim.state[:3] = [ENGAGED,WALK,FREE]
        sim.goal[0] = ('study',home,-1)
        self.assertAlmostEqual(sim.on_campus_return_share(),1/3)


if __name__=='__main__': unittest.main()
