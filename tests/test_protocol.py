import copy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nextbus.config import Config
from nextbus.data import Dataset
from nextbus.protocol import (ExperimentSimulation, ExperimentLLM, NetworkRule, before_events,
                              before_decision, totals, difference, validate_action, run_day)


class ProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = Config(population=120,start=360,end=1440,decision_interval=15)
        cls.data = Dataset(cls.config)

    def test_multi_route_action_is_atomic(self):
        sim = ExperimentSimulation(self.data,self.config)
        with self.assertRaises(ValueError):
            sim.execute({'routes':['A1','A1']})
        self.assertEqual(len(sim.dispatch_log),0)
        sim.execute({'routes':['A1','D1']})
        self.assertEqual([r['route'] for r in sim.dispatch_log],['A1','D1'])

    def test_half_open_boundary_and_service_carryover(self):
        sim = ExperimentSimulation(self.data,self.config)
        sim.events = []
        sim.t = 420
        sim.execute({'routes':['A1']})
        before = totals(sim)
        before_events(sim,435)
        after = totals(sim)
        metrics = difference(before,after,88)
        self.assertAlmostEqual(metrics['extra_bus_hours'],.25)
        self.assertEqual(metrics['extra_buses'],0)
        self.assertEqual(metrics['extra_utilization_pct'],0)

    def test_counterfactual_clone_replay(self):
        sim = ExperimentSimulation(self.data,self.config)
        sim.controller = NetworkRule()
        before_decision(sim,480)
        a,b = sim.clone(),sim.clone()
        a.controller,b.controller = copy.deepcopy(sim.controller),copy.deepcopy(sim.controller)
        before_events(a,540)
        before_events(b,540)
        self.assertEqual(totals(a),totals(b))
        self.assertEqual(a.dispatch_log,b.dispatch_log)

    def test_information_boundary(self):
        sim = ExperimentSimulation(self.data,self.config)
        before_decision(sim,480)
        example = dict(case_id='x',snapshot=sim.observation(),candidate_outcomes=[],
                       preferred_action_index=0,horizon_minutes=60,history=[{'time':420}],dispatch_events=[])
        for arm in ('LLM-A','LLM-B','LLM-C','LLM-D'):
            payload = ExperimentLLM(replace(self.config,arm=arm),[example]*4).input(sim)
            self.assertEqual('history' in payload,arm in ('LLM-B','LLM-D'))
            self.assertEqual('examples' in payload,arm in ('LLM-C','LLM-D'))
            if 'history' in payload:
                self.assertEqual([h['time'] for h in payload['history']],[420,435,450,465])
            if 'examples' in payload:
                self.assertEqual('history' in payload['examples'][0],arm=='LLM-D')
            for vehicle in payload['current']['vehicles']:
                self.assertNotIn('load',vehicle)

    def test_full_day_exports_six_rows(self):
        with tempfile.TemporaryDirectory() as directory, patch('nextbus.protocol.ROOT',Path(directory)):
            _,rows = run_day(self.data,self.config,Path(directory)/'run')
            self.assertEqual(len(rows),6)
            self.assertTrue(all(r['extra_buses']==0 for r in rows))
            self.assertTrue(all(r['extra_utilization_pct'] is None for r in rows))
            self.assertTrue((Path(directory)/'run'/'manifest.json').exists())


if __name__=='__main__':
    unittest.main()
