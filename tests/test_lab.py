from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from nextbus.config import Config
from nextbus.data import Dataset
from nextbus.lab import Lab, BoundedLLM
from nextbus.dispatch_rules import CustomRule
from nextbus.experiments import save_json
from nextbus.protocol import ExperimentSimulation, NetworkRule, before_events, totals


class LabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config=Config(population=120,day='2026-08-24',decision_interval=15)
        cls.data=Dataset(cls.config)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.patch=patch('nextbus.lab.LAB',self.root)
        self.patch.start();self.addCleanup(self.patch.stop)

    def experiment(self, **changes):
        return dict(dict(name='Morning test',dispatcher_id='preset-base0',days=['2026-08-24'],
                         mode='window',start=420,end=450,seed=101),**changes)

    def run_saved(self,lab,experiment):
        with patch('nextbus.lab.Thread') as thread:
            lab.start({'experiment_id':experiment['id'],'api_key':'must-not-persist'})
            args=thread.call_args.kwargs['args']
        with patch('nextbus.lab.load_setup',return_value=(self.config,self.data,{})):
            lab.run(*args)
        self.assertEqual(lab.job['status'],'complete',lab.job)

    def test_window_replay_upsert_and_credential_exclusion(self):
        lab=Lab(self.data,self.config)
        exp=lab.experiment(self.experiment())
        self.run_saved(lab,exp)
        row=lab.status()['results'][0]
        self.assertEqual(row['experiment_name'],'Morning test')
        self.assertEqual(row['frames'],7)
        reference=ExperimentSimulation(self.data,replace(self.config,seed=101))
        before_events(reference,420)
        self.assertEqual(lab.frame(row['id'],0)['people'],reference.snapshot()['people'])
        self.assertEqual(lab.frame(row['id'],0)['evaluation']['total_wait'],0)
        self.assertEqual(lab.frame(row['id'],6)['time'],450)
        self.assertIn(b'experiment_name',lab.csv())
        self.run_saved(lab,exp)
        self.assertEqual(len(lab.status()['results']),1)
        self.assertNotEqual(lab.status()['results'][0]['id'],row['id'])
        self.assertTrue((self.root/'runs'/row['id']).exists())
        second=lab.experiment(self.experiment(name='Independent experiment'))
        self.run_saved(lab,second)
        self.assertEqual(len(lab.status()['results']),2)
        for file in self.root.rglob('*.json'):
            self.assertNotIn('must-not-persist',file.read_text())
        lab.discard({'id':exp['id'],'type':'experiment'})
        self.assertEqual(len(lab.status()['results']),2)
        self.assertEqual(len(Lab(self.data,self.config).experiments),1)

    def test_replay_requires_current_saved_experiment_and_dispatcher(self):
        lab=Lab(self.data,self.config)
        rule=lab.profile(dict(name='Custom',kind='RULE'))
        exp=lab.experiment(self.experiment(dispatcher_id=rule['id'],days=['2026-08-24','2026-08-26']))
        self.run_saved(lab,exp)
        self.assertTrue(all(r['replay_current'] for r in lab.status()['results']))
        lab.profile(dict(rule,rule_share=.5))
        self.assertFalse(any(r['replay_current'] for r in lab.status()['results']))
        exp=lab.experiment(dict(exp,days=['2026-08-26'],seed=42))
        self.run_saved(lab,exp)
        row,=lab.status()['results']
        self.assertTrue(row['replay_current'])
        self.assertEqual((row['day'],row['seed'],row['profile']['rule_share']),('2026-08-26',42,.5))
        lab.experiment(dict(exp,seed=43))
        self.assertFalse(lab.status()['results'][0]['replay_current'])

    def test_default_dispatchers_and_protected_base0(self):
        lab=Lab(self.data,self.config)
        self.assertEqual([d['name'] for d in lab.profiles],['BASE0','BASE1','LLM-A','LLM-B','LLM-C','LLM-D'])
        with self.assertRaises(ValueError):lab.discard({'type':'profile','id':'preset-base0'})
        with self.assertRaises(ValueError):lab.profile(dict(id='preset-base0',name='replace',kind='RULE'))
        lab.discard({'type':'profile','id':'preset-llma'})
        self.assertNotIn('preset-llma',[d['id'] for d in Lab(self.data,self.config).profiles])
        custom=lab.profile(dict(id='preset-base1',name='BASE1',kind='RULE',rule_share=.5,minimum_buses=3))
        self.assertNotEqual(custom['id'],'preset-base1')
        self.assertFalse(custom['baseline'])
        self.assertTrue(next(d for d in lab.profiles if d['id']=='preset-base1')['baseline'])

    def test_experiment_validation_and_deleted_dispatcher(self):
        lab=Lab(self.data,self.config)
        for changes in ({'start':419},{'start':240},{'end':405},{'end':420},{'end':1455},{'days':[]},{'dispatcher_id':'missing'}):
            with self.assertRaises(ValueError):lab.experiment(self.experiment(**changes))
        exp=lab.experiment(self.experiment(dispatcher_id='preset-base1'))
        lab.discard({'type':'profile','id':'preset-base1'})
        with self.assertRaisesRegex(ValueError,'saved dispatcher'):lab.start({'experiment_id':exp['id']})
        for changes in ({'memory':121},{'context_tokens':8000},{'example_ids':['missing']}):
            with self.assertRaises(ValueError):lab.profile(dict(name='Test',kind='LLM',**changes))
        for changes in ({'crowd_level':'FULL'},{'rule_share':1.1},{'minimum_buses':0}):
            with self.assertRaises(ValueError):lab.profile(dict(name='Rule',kind='RULE',**changes))

    def test_legacy_profiles_migrate_without_removing_files(self):
        original=[dict(id='old',name='Earlier test',kind='BASE1',memory=0,example_ids=[],
                       days=['2026-08-24'],start=420,end=450,mode='window',seed=101)]
        save_json(self.root/'profiles.json',original)
        lab=Lab(self.data,self.config)
        self.assertEqual(lab.experiments[0]['dispatcher_id'],'old')
        self.assertEqual(lab.experiments[0]['name'],'Earlier test')
        self.assertEqual(json.loads((self.root/'profiles.json').read_text()),original)
        self.assertEqual(len(Lab(self.data,self.config).experiments),1)

    def test_discard_examples_preserves_completed_history(self):
        frozen=self.root/'frozen'
        default=dict(case_id='default',day='2027-01-25')
        custom=dict(case_id='custom',day='2027-02-10')
        save_json(frozen/'examples.json',{'cases':[default]})
        save_json(self.root/'examples.json',[custom])
        completed=[dict(id='completed',examples=[default,custom],example_count=2)]
        save_json(self.root/'results.json',completed)
        original=(frozen/'examples.json').read_bytes()
        with patch('nextbus.lab.ROOT',frozen):
            lab=Lab(self.data,self.config)
            profile=lab.profile(dict(name='Examples',kind='LLM',example_ids=['default','custom']))
            lab.job={'status':'running'}
            with self.assertRaisesRegex(ValueError,'current job'):lab.discard({'type':'example','id':'default'})
            lab.job={'status':'ready'}
            for example_id in ('default','custom'):
                lab.discard({'type':'example','id':example_id})
            restarted=Lab(self.data,self.config)
            self.assertEqual(restarted.status()['examples'],[])
            self.assertEqual(next(d for d in restarted.profiles if d['id']==profile['id'])['example_ids'],[])
            self.assertEqual(read_results:=json.loads((self.root/'results.json').read_text()),completed)
            self.assertEqual((frozen/'examples.json').read_bytes(),original)

    def test_training_test_overlap_is_checked_on_experiment(self):
        lab=Lab(self.data,self.config)
        examples=lab.status()['examples']
        if not examples:self.skipTest('No example fixtures')
        profile=lab.profile(dict(name='Few shot',kind='LLM',example_ids=[examples[0]['case_id']]))
        with self.assertRaisesRegex(ValueError,'disjoint'):
            lab.experiment(self.experiment(dispatcher_id=profile['id'],days=[examples[0]['day']]))

    def test_custom_rule_counts_total_observed_buses(self):
        vehicles=[dict(route_id='A',run_id='one',crowd_level='HIGH'),dict(route_id='A',run_id='two',crowd_level='LOW')]
        sim=SimpleNamespace(t=30,history=[{'time':15,'vehicles':vehicles}],
            data=SimpleNamespace(routes={'A':[0,1]}),config=Config(),
            observation=lambda:dict(extra_bus_commitments=[]))
        self.assertEqual(CustomRule('HIGH',.5,2).decide(sim),{'routes':['A']})
        self.assertEqual(NetworkRule().decide(sim),{'routes':[]})
        self.assertEqual(CustomRule('HIGH',.7,2).decide(sim),{'routes':[]})
        self.assertEqual(CustomRule('HIGH',.5,3).decide(sim),{'routes':[]})
        rule=CustomRule('LOW',1,2)
        self.assertEqual(rule.decide(sim),{'routes':['A']})
        self.assertEqual(rule.decide(sim),{'routes':[]})

    def test_memory_and_prompt_budget(self):
        sim=ExperimentSimulation(self.data,self.config);before_events(sim,480)
        llm=BoundedLLM(replace(self.config,arm='LLM-B',history_minutes=30),[],'unused',32768)
        self.assertEqual([h['time'] for h in llm.input(sim)['history']],[450,465])
        llm.config=replace(llm.config,history_minutes=120)
        self.assertEqual(len(llm.input(sim)['history']),8)
        llm.context_tokens=8192
        with self.assertRaises(ValueError):llm.input(sim)

    def test_diagnostics_do_not_change_baseline(self):
        sim=ExperimentSimulation(self.data,self.config);sim.controller=NetworkRule();before_events(sim,480)
        before=json.dumps(sim.controller.__dict__,sort_keys=True);clone=sim.clone();sim.snapshot()
        self.assertEqual(before,json.dumps(sim.controller.__dict__,sort_keys=True))
        before_events(sim,540);before_events(clone,540)
        self.assertEqual(totals(sim),totals(clone));self.assertEqual(sim.dispatch_log,clone.dispatch_log)

    def test_hourly_metrics_are_interval_totals(self):
        sim=ExperimentSimulation(self.data,self.config);before_events(sim,485);rows=sim.snapshot()['hourly']
        self.assertEqual([(r['start'],r['end']) for r in rows],[(360,420),(420,480),(480,485)])
        self.assertAlmostEqual(sum(r['total_wait'] for r in rows),sim.wait_total)
        self.assertEqual(sum(r['denied_attempts'] for r in rows),sim.denials)


if __name__=='__main__':unittest.main()
