"""Synthetic tool-loop contracts, genuine message shapes, persistent pauses and budgets."""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage,ToolMessage
from tcm.research_task import NativeToolModel,ResearchTaskEngine,ScriptedToolModel,report
from tcm.research_task_fixtures import NAMES,SyntheticResearchTools
from tcm.research_task_store import ResearchTaskStore,TaskConflict


class FakeNativeSDK:
    def __init__(self):self.requests=[];self.tool_names=[]
    def bind_tools(self,tools,**kwargs):
        self.tool_names=[t['function']['name'] for t in tools]
        assert kwargs=={'tool_choice':'auto','parallel_tool_calls':False}
        return self
    def invoke(self,messages):
        self.requests.append(messages)
        task=json.loads(messages[1].content)
        decision=ScriptedToolModel().decide(task)
        return AIMessage(content='不应保存或展示的模型自由文本',tool_calls=[
            {'name':decision['name'],'args':decision['args'],'id':'native-'+str(len(self.requests)),'type':'tool_call'}],
            usage_metadata={'input_tokens':10,'output_tokens':5,'total_tokens':15})


class ResearchTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'tasks.sqlite3';self.store=ResearchTaskStore(self.path)

    def engine(self,profile='retry',model=None):
        return ResearchTaskEngine(self.store,model or ScriptedToolModel(),SyntheticResearchTools(profile))

    def choose_sources(self,engine,t):
        return engine.choose(t['id'],{NAMES[0]:'SYN-A1',NAMES[1]:'SYN-B1'},revision=t['revision'])

    def to_targets(self,engine,t=None):
        t=engine.run((t or engine.create())['id']);self.assertEqual(t['status'],'waiting_sources')
        t=self.choose_sources(engine,t);t=engine.run(t['id']);self.assertEqual(t['status'],'waiting_targets')
        return t

    def complete(self,engine,targets=None):
        t=self.to_targets(engine)
        t=engine.choose(t['id'],targets or ['SYN-T1'],revision=t['revision'])
        return engine.run(t['id'])

    def test_native_tool_protocol_resumes_two_pauses_and_keeps_evidence_scopes(self):
        sdk=FakeNativeSDK();engine=self.engine(model=NativeToolModel(sdk))
        t=engine.run(engine.create()['id']);self.assertEqual(t['status'],'waiting_sources')
        # Actual DB close/reopen; a new engine and model wrapper use the same task.
        restored=ResearchTaskStore(self.path);engine=ResearchTaskEngine(restored,NativeToolModel(sdk),SyntheticResearchTools())
        t=self.choose_sources(engine,restored.get(t['id']));t=engine.run(t['id'])
        self.assertEqual(t['status'],'waiting_targets')
        t=engine.choose(t['id'],['SYN-T1'],revision=t['revision']);t=engine.run(t['id'])
        self.assertEqual(t['status'],'complete');self.assertEqual((t['model_calls'],t['tool_calls']),(7,7))
        self.assertEqual(len(sdk.requests),7)
        self.assertTrue(any(isinstance(m,ToolMessage) for m in sdk.requests[-1]))
        self.assertNotIn('不应保存或展示',json.dumps(t,ensure_ascii=False))
        self.assertEqual({r['classification'] for r in t['report']['study_evidence']},{'herb_source_record','ingredient_only'})
        self.assertEqual([s['variant'] for s in t['searches']],['initial','expanded'])
        self.assertTrue(all(e['usage']['total_tokens']==15 for e in t['events'] if e['kind']=='tool'))

    def test_fixed_driver_is_partial_on_empty_but_adaptive_driver_can_collect_evidence(self):
        fixed=self.complete(self.engine(model=ScriptedToolModel(adaptive=False)))
        adaptive=self.complete(self.engine())
        self.assertEqual(fixed['status'],'partial');self.assertEqual(adaptive['status'],'complete')
        self.assertEqual((fixed['tool_calls'],adaptive['tool_calls']),(4,7))
        self.assertTrue(fixed['report']['gaps'])

    def test_withdrawn_ingredient_and_wrong_object_never_complete_herb_evidence(self):
        for profile,classification in [('withdrawn','excluded_retracted'),('ingredient_only','ingredient_only'),('wrong_object','excluded_wrong_object')]:
            with self.subTest(profile=profile):
                t=self.complete(self.engine(profile))
                self.assertEqual(t['status'],'partial');self.assertTrue(t['report']['gaps'])
                self.assertTrue(all(s['classification']==classification for s in t['studies'].values()))

    def test_unavailable_and_no_common_sources_do_not_become_no_real_world_relation(self):
        t=self.complete(self.engine('unavailable'));self.assertEqual(t['status'],'partial')
        self.assertEqual(t['searches'][0]['status'],'unavailable');self.assertFalse(t['studies'])
        e=self.engine('no_common');t=e.run(e.create()['id']);t=self.choose_sources(e,t);t=e.run(t['id'])
        self.assertEqual(t['status'],'partial');self.assertFalse(t['relations'])

    def test_timeout_is_a_tool_observation_and_can_finish_partial(self):
        e=self.engine('direct')
        def timeout(*a):raise TimeoutError('secret-like exception text must not be returned')
        with patch.object(e.tools,'search',side_effect=timeout):t=self.complete(e)
        self.assertEqual(t['status'],'partial');self.assertEqual(t['searches'][0]['reason'],'synthetic_tool_timeout')
        self.assertNotIn('secret-like',json.dumps(t))

    def test_unknown_source_target_and_stale_choice_do_not_modify_task(self):
        e=self.engine();t=e.run(e.create()['id']);before=deepcopy(t)
        with self.assertRaises(ValueError):e.choose(t['id'],{NAMES[0]:'SYN-OTHER',NAMES[1]:'SYN-B1'},revision=t['revision'])
        self.assertEqual(self.store.get(t['id']),before)
        chosen=self.choose_sources(e,t)
        with self.assertRaises(TaskConflict):e.choose(t['id'],{NAMES[0]:'SYN-A1',NAMES[1]:'SYN-B1'},revision=t['revision'])
        t=e.run(chosen['id']);before=deepcopy(t)
        with self.assertRaises(ValueError):e.choose(t['id'],['SYN-FAKE'],revision=t['revision'])
        self.assertEqual(self.store.get(t['id']),before)

    def test_foreign_ids_and_hallucinated_evidence_are_rejected(self):
        e=self.engine();t=self.to_targets(e);t=e.choose(t['id'],['SYN-T1'],revision=t['revision'])
        for name,args in [('search_literature',{'target_id':'SYN-OTHER','variant':'initial'}),
                          ('inspect_study',{'study_id':'SYN-T1-H'}),
                          ('common_targets',{'source_ids':['SYN-OTHER','SYN-B1']}),
                          ('search_literature',{'target_id':'SYN-T1','variant':'initial','sql':'select secret'}),
                          ('shell',{'command':'anything'})]:
            with self.assertRaises((ValueError,KeyError)):e.execute(deepcopy(t),name,args)
        self.assertFalse(self.store.get(t['id'])['studies'])

    def test_two_invalid_or_duplicate_actions_stop_without_repeating_tool(self):
        class Bad:
            def decide(self,t):return {'name':'search_literature','args':{'target_id':'SYN-T1','variant':'initial'}}
        e=self.engine();t=self.to_targets(e);t=e.choose(t['id'],['SYN-T1'],revision=t['revision']);e.model=Bad()
        with patch.object(e.tools,'search',wraps=e.tools.search) as search:t=e.run(t['id'])
        self.assertEqual(t['status'],'failed');self.assertEqual(search.call_count,1)
        self.assertEqual(t['invalid_actions'],2)

    def test_budget_is_persistent_and_cannot_be_reset_by_reopening(self):
        e=self.engine();t=e.create(max_model_calls=1);t=e.run(t['id']);self.assertEqual(t['status'],'waiting_sources')
        t=self.choose_sources(e,t)
        new=ResearchTaskEngine(ResearchTaskStore(self.path),ScriptedToolModel(),SyntheticResearchTools())
        t=new.run(t['id']);self.assertEqual(t['status'],'budget_exhausted');self.assertEqual(t['model_calls'],1)
        for kwargs in ({'max_model_calls':True},{'max_tool_calls':13},{'max_active_ms':0}):
            with self.assertRaises(ValueError):e.create(**kwargs)

    def test_active_time_budget_stops_before_dispatching_slow_model_action(self):
        class Slow:
            def decide(self,t):time.sleep(.005);return {'name':'resolve_sources','args':{'names':NAMES}}
        e=self.engine(model=Slow());t=e.run(e.create(max_active_ms=1)['id'])
        self.assertEqual(t['status'],'budget_exhausted');self.assertEqual(t['tool_calls'],0)

    def test_crashed_reserved_model_call_requires_explicit_recovery_and_keeps_charge(self):
        class Crash:
            def decide(self,t):raise KeyboardInterrupt()
        e=self.engine(model=Crash());task=e.create()
        with self.assertRaises(KeyboardInterrupt):e.run(task['id'])
        t=self.store.get(task['id']);self.assertEqual(t['model_calls'],1);self.assertTrue(t['inflight'])
        e=self.engine();t=e.run(t['id']);self.assertEqual(t['status'],'interrupted')
        t=e.retry_interrupted(t['id'],revision=t['revision']);t=e.run(t['id'])
        self.assertEqual(t['status'],'waiting_sources');self.assertEqual(t['model_calls'],2)

    def test_store_conflicts_unrelated_db_and_delete(self):
        e=self.engine();a=e.create();b=self.store.get(a['id']);self.store.save(a)
        with self.assertRaises(TaskConflict):self.store.save(b)
        old=Path(self.temp.name)/'other.sqlite3'
        with sqlite3.connect(old) as db:db.execute('CREATE TABLE private_data(value TEXT)')
        before=hashlib.sha256(old.read_bytes()).hexdigest()
        with self.assertRaises(ValueError):ResearchTaskStore(old)
        self.assertEqual(hashlib.sha256(old.read_bytes()).hexdigest(),before)
        self.assertEqual(self.path.stat().st_mode&0o777,0o600)
        self.store.delete(a['id'],a['revision'])
        with self.assertRaises(KeyError):self.store.get(a['id'])

    def test_fixture_binding_change_refuses_resume(self):
        e=self.engine();t=e.create();e.tools.fingerprint='changed'
        with self.assertRaises(ValueError):e.run(t['id'])
        self.assertEqual(self.store.get(t['id'])['model_calls'],0)

    def test_multiple_native_calls_or_free_text_do_not_execute_tools(self):
        class InvalidSDK(FakeNativeSDK):
            def invoke(self,messages):return AIMessage(content='这里直接编造答案')
        e=self.engine(model=NativeToolModel(InvalidSDK()));t=e.run(e.create()['id'])
        self.assertEqual(t['status'],'failed');self.assertEqual(t['tool_calls'],0)
        self.assertFalse(t['report']['complete'])
        class ParallelSDK(FakeNativeSDK):
            def invoke(self,messages):return AIMessage(content='',tool_calls=[
                {'name':'finish_task','args':{},'id':str(n),'type':'tool_call'} for n in range(2)])
        e=self.engine(model=NativeToolModel(ParallelSDK()));t=e.run(e.create()['id'])
        self.assertEqual(t['tool_calls'],0)

    def test_independent_native_inspections_are_bounded_and_replayed_as_one_message(self):
        class BatchSDK(FakeNativeSDK):
            def invoke(self,messages):
                task=json.loads(messages[1].content)
                ids=[r['id'] for q in task['searches'] for r in q['records'] if r['id'] not in task['studies']]
                if len(ids)==2:
                    self.requests.append(messages)
                    return AIMessage(content='',tool_calls=[{'name':'inspect_study','args':{'study_id':identifier},
                         'id':'batch-'+str(i),'type':'tool_call'} for i,identifier in enumerate(ids)])
                return super().invoke(messages)
        sdk=BatchSDK();t=self.complete(self.engine(model=NativeToolModel(sdk)))
        self.assertEqual(t['status'],'complete');self.assertEqual(t['tool_calls'],7);self.assertEqual(t['model_calls'],6)
        self.assertTrue(any(isinstance(m,AIMessage) and len(m.tool_calls)==2 for m in sdk.requests[-1]))
        self.assertEqual(sum(isinstance(m,ToolMessage) for m in sdk.requests[-1]),6)

    def test_explicit_model_recovery_keeps_reserved_attempts_and_prior_failure(self):
        class Fail:
            def decide(self,t):raise ValueError('protocol')
        e=self.engine(model=Fail());t=e.run(e.create()['id']);self.assertEqual(t['status'],'failed')
        e=self.engine();t=e.retry_failed_model_step(t['id'],revision=t['revision'])
        t=e.run(t['id']);self.assertEqual(t['status'],'waiting_sources');self.assertEqual(t['model_calls'],2)
        self.assertTrue(any(x['kind']=='recovery' and x['previous_failure']['error_type']=='ValueError' for x in t['events']))

    def test_existing_result_path_is_rejected_before_any_native_model_creation(self):
        import sys
        from scripts.run_research_task import main
        out=Path(self.temp.name)/'existing-result.json';out.write_text('keep-original')
        args=['run_research_task','demo','--policy','native','--live-synthetic',
              '--database',str(self.path),'--out',str(out)]
        with patch.object(sys,'argv',args),patch('scripts.run_research_task.controller',side_effect=AssertionError('Provider created')):
            with self.assertRaises(FileExistsError):main()
        self.assertEqual(out.read_text(),'keep-original')

    def test_premature_finish_has_gaps_and_two_confirmed_targets_can_complete(self):
        class FinishNow:
            def decide(self,t):return {'name':'finish_task','args':{}}
        e=self.engine(model=FinishNow());t=e.run(e.create()['id'])
        self.assertEqual(t['status'],'partial');self.assertFalse(t['report']['complete'])
        t=self.complete(self.engine(),['SYN-T1','SYN-T2'])
        self.assertEqual(t['status'],'complete');self.assertEqual(len(t['studies']),4)
        self.assertEqual(t['tool_calls'],11)


if __name__=='__main__':unittest.main()
