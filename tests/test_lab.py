import ast
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nextbus.config import Config
from nextbus.data import Dataset
from nextbus.lab import Lab, BoundedLLM
from nextbus.experiments import save_json
from nextbus.protocol import ExperimentSimulation, NetworkRule, before_events, totals


class LabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config=Config(population=120,day='2026-08-24',decision_interval=15)
        cls.data=Dataset(cls.config)

    def profile(self, **kwargs):
        return dict(name='Test',kind='BASE1',days=['2026-08-24'],mode='window',start=420,end=450,
                    memory=0,example_ids=[],**kwargs)

    def test_window_replay_export_and_credential_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp, patch('nextbus.lab.LAB',Path(tmp)), patch('nextbus.lab.load_setup',return_value=(self.config,self.data,{})):
            lab=Lab(self.data,self.config)
            profile=lab.profile(dict(self.profile(),api_key='must-not-persist'))
            lab.run(profile,'must-not-persist')
            self.assertEqual(lab.job['status'],'complete',lab.job)
            row=lab.status()['results'][0]
            self.assertEqual(row['frames'],7)
            frame=lab.frame(row['id'],0)
            reference=ExperimentSimulation(self.data,replace(self.config,seed=profile['seed']))
            before_events(reference,420)
            self.assertEqual(frame['people'],reference.snapshot()['people'])
            self.assertEqual(frame['evaluation']['total_wait'],0)
            self.assertEqual(lab.frame(row['id'],6)['time'],450)
            self.assertIn(b'example_details',lab.csv())
            for file in Path(tmp).rglob('*.json'):
                self.assertNotIn('must-not-persist',file.read_text())
            lab.discard({'id':row['id'],'type':'result'})
            self.assertEqual(lab.status()['results'],[])
            self.assertTrue((Path(tmp)/'runs'/row['id']).exists())

    def test_validation(self):
        lab=Lab(self.data,self.config)
        for changes in ({'memory':121},{'start':419},{'start':240},{'end':405},{'end':420},{'end':1455},{'days':[]},{'context_tokens':8000}):
            with self.assertRaises(ValueError): lab.validate(dict(self.profile(),**changes))

    def test_discard_examples_persists_without_changing_completed_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, runtime = Path(tmp)/'frozen', Path(tmp)/'lab'
            default = dict(case_id='default',day='2027-01-25')
            custom = dict(case_id='custom',day='2027-02-10')
            save_json(root/'examples.json',{'cases':[default]})
            save_json(runtime/'examples.json',[custom])
            completed = [dict(id='completed',examples=[default,custom],example_count=2)]
            save_json(runtime/'results.json',completed)
            original = (root/'examples.json').read_bytes()
            with patch('nextbus.lab.ROOT',root), patch('nextbus.lab.LAB',runtime):
                lab = Lab(self.data,self.config)
                lab.profile(dict(self.profile(),example_ids=['default','custom']))
                lab.job={'status':'running'}
                with self.assertRaisesRegex(ValueError,'current job'):
                    lab.discard({'type':'example','id':'default'})
                self.assertEqual(len(lab.status()['examples']),2)
                lab.job={'status':'ready'}
                for example_id in ('default','custom'):
                    lab.discard({'type':'example','id':example_id})
                    with self.assertRaisesRegex(ValueError,'Unknown example'):
                        lab.validate(dict(self.profile(),example_ids=[example_id]))
                restarted = Lab(self.data,self.config)
                self.assertEqual(restarted.status()['examples'],[])
                self.assertEqual(restarted.profiles[0]['example_ids'],[])
                self.assertEqual(restarted.status()['results'],completed)
                self.assertEqual((root/'examples.json').read_bytes(),original)
                self.assertEqual(json.loads((runtime/'examples.json').read_text()),[custom])

    def test_memory_and_prompt_budget(self):
        sim=ExperimentSimulation(self.data,self.config)
        before_events(sim,480)
        llm=BoundedLLM(replace(self.config,arm='LLM-B',history_minutes=30),[],'unused',32768)
        self.assertEqual([h['time'] for h in llm.input(sim)['history']],[450,465])
        llm.config=replace(llm.config,history_minutes=120)
        self.assertEqual(len(llm.input(sim)['history']),8)
        llm.context_tokens=8192
        with self.assertRaises(ValueError): llm.input(sim)

    def test_diagnostics_do_not_change_baseline(self):
        sim=ExperimentSimulation(self.data,self.config)
        sim.controller=NetworkRule()
        before_events(sim,480)
        before=json.dumps(sim.controller.__dict__,sort_keys=True)
        clone=sim.clone()
        sim.snapshot()
        self.assertEqual(before,json.dumps(sim.controller.__dict__,sort_keys=True))
        before_events(sim,540);before_events(clone,540)
        self.assertEqual(totals(sim),totals(clone))
        self.assertEqual(sim.dispatch_log,clone.dispatch_log)

    def test_hourly_metrics_are_interval_totals(self):
        sim=ExperimentSimulation(self.data,self.config)
        before_events(sim,485)
        rows=sim.snapshot()['hourly']
        self.assertEqual([(r['start'],r['end']) for r in rows],[(360,420),(420,480),(480,485)])
        self.assertAlmostEqual(sum(r['total_wait'] for r in rows),sim.wait_total)
        self.assertEqual(sum(r['denied_attempts'] for r in rows),sim.denials)


if __name__=='__main__': unittest.main()
