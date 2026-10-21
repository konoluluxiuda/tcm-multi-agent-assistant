"""Synthetic-only planner protocol, ambiguity, human review and evaluation integrity."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage
from tcm.task_planner import NativeTaskPlanner, conservative_plan, create_proposal
from tcm.research_task import ResearchTaskEngine, ScriptedToolModel
from tcm.research_task_fixtures import SyntheticResearchTools,NAMES
from tcm.research_task_store import ResearchTaskStore
from scripts.eval_task_planner import CASES,score,main,expected,exercise_proposal


class SDK:
    def __init__(self,args=None,reply=None,error=None):self.args=args;self.reply=reply;self.error=error;self.calls=0
    def bind_tools(self,tools,**kw):self.tools=tools;self.kw=kw;return self
    def invoke(self,messages):
        self.calls+=1;self.messages=messages
        if self.error:raise self.error
        return self.reply if self.reply is not None else AIMessage(content='FREE THOUGHT MUST NOT BE STORED',
            tool_calls=[{'name':'submit_task_plan','args':self.args or {},'id':'test','type':'tool_call'}])


class TaskPlannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=ResearchTaskStore(Path(self.tmp.name)/'tasks.sqlite3')
        self.engine=ResearchTaskEngine(self.store,ScriptedToolModel(),SyntheticResearchTools('direct'))

    def test_known_baseline_cases_are_explicit_and_never_count_as_model(self):
        for case in CASES:
            result=conservative_plan(case['question'])
            self.assertTrue(score(result,case)['validated_goal_entities_match'],case['id'])
            self.assertEqual(result.calls,0);self.assertEqual(result.provider_calls,0)

    def test_native_schema_call_contains_only_question_and_fixed_system(self):
        sdk=SDK({'intent':'source_relations','herb_one':NAMES[0],'herb_two':NAMES[1]})
        r=NativeTaskPlanner(sdk).plan(CASES[0]['question'])
        self.assertEqual(r.status,'ok');self.assertEqual(sdk.calls,1)
        self.assertEqual(len(sdk.messages),2);self.assertEqual(sdk.messages[1].content,CASES[0]['question'])
        self.assertEqual(len(sdk.tools),1);self.assertFalse(sdk.kw['parallel_tool_calls'])
        self.assertEqual(r.calls,1);self.assertEqual(r.provider_calls,0)
        self.assertNotIn('FREE THOUGHT',r.model_dump_json())

    def test_unmentioned_entity_is_removed_but_raw_failure_is_retained(self):
        case=CASES[5];sdk=SDK({'intent':'source_relations','herb_one':NAMES[0],'herb_two':NAMES[1]})
        r=NativeTaskPlanner(sdk).plan(case['question'])
        self.assertIsNone(r.plan.herb_two);self.assertEqual(r.raw_plan.herb_two,NAMES[1])
        self.assertTrue(r.grounded_entities_removed)
        self.assertFalse(score(r,case)['raw_goal_entities_match'])
        self.assertTrue(score(r,case)['validated_goal_entities_match'])

    def test_bad_protocol_or_schema_falls_back_to_missing_scope_not_execution(self):
        replies=[AIMessage(content='no tool'),
            AIMessage(content='',tool_calls=[{'name':'delete_data','args':{},'id':'1','type':'tool_call'}]),
            AIMessage(content='',tool_calls=[{'name':'submit_task_plan','args':{},'id':str(i),'type':'tool_call'} for i in range(2)])]
        for reply in replies:
            r=NativeTaskPlanner(SDK(reply=reply)).plan(CASES[0]['question'])
            self.assertEqual(r.status,'invalid_output');self.assertIsNone(r.plan.intent)
        for args in ({'data_mode':'etcm_local'},{'tool':'search_literature'},
                     {'intent':'diagnosis'},{'herb_one':'白术'},{'herb_one':NAMES[0],'herb_two':NAMES[0]}):
            r=NativeTaskPlanner(SDK(args)).plan(CASES[0]['question'])
            self.assertEqual(r.status,'invalid_output');self.assertIsNone(r.plan.intent)

    def test_timeout_no_retry_no_exception_text_and_model_attempt_is_counted(self):
        sdk=SDK(error=TimeoutError('private API key and URL'))
        r=NativeTaskPlanner(sdk,live_provider=True).plan(CASES[0]['question'])
        self.assertEqual(r.status,'unavailable');self.assertEqual(sdk.calls,1)
        self.assertEqual(r.provider_calls,1);self.assertNotIn('API key',r.model_dump_json())
        self.assertIsNone(r.plan.intent)

    def test_input_budget_checked_before_invoke(self):
        sdk=SDK();planner=NativeTaskPlanner(sdk)
        for question in ('',None,'x'*401):
            with self.assertRaises(ValueError):planner.plan(question)
        self.assertEqual(sdk.calls,0)

    def test_complete_proposal_waits_for_review_and_can_be_corrected(self):
        r=NativeTaskPlanner(SDK({'intent':'evidence_review','herb_one':NAMES[0],'herb_two':NAMES[1]})).plan(CASES[0]['question'])
        t=create_proposal(self.engine,CASES[0]['question'],r);t=self.engine.run(t['id'])
        self.assertEqual(t['status'],'waiting_plan');self.assertEqual(t['tool_calls'],0)
        self.assertEqual(len(t['pending']['options']),3);self.assertTrue(t['plan_review_required'])
        before=deepcopy(t)
        with self.assertRaises(ValueError):self.engine.execute(t,'resolve_sources',{'names':NAMES})
        self.assertEqual(t,before)
        selection={'intent':'source_relations','herb_one':NAMES[0],'herb_two':NAMES[1]}
        t=self.engine.choose(t['id'],selection,revision=t['revision'])
        self.assertFalse(t['plan_review_required']);self.assertEqual(t['plan']['intent'],'source_relations')
        self.assertEqual(t['proposed_plan']['intent'],'evidence_review')
        t=self.engine.run(t['id']);self.assertEqual(t['status'],'waiting_sources');self.assertEqual(t['sources'],[])
        t=self.engine.choose(t['id'],{NAMES[0]:'SYN-A1',NAMES[1]:'SYN-B1'},revision=t['revision'])
        t=self.engine.run(t['id']);t=self.engine.choose(t['id'],['SYN-T1'],revision=t['revision'])
        t=self.engine.run(t['id'])
        from tcm.research_task_report import markdown
        self.assertIn('原规划提案（用户确认前）',markdown(t))

    def test_incomplete_review_cannot_sneak_past_missing_slots(self):
        r=conservative_plan('帮我比较示例甲草和示例乙草。')
        t=create_proposal(self.engine,'帮我比较示例甲草和示例乙草。',r)
        with self.assertRaises(ValueError):self.engine.choose(t['id'],{'intent':'source_relations'},revision=t['revision'])
        self.assertEqual(self.store.get(t['id']),t)

    def test_offline_comparison_is_not_mislabeled_live_or_independent(self):
        out=Path(self.tmp.name)/'offline'
        with patch('sys.argv',['eval','--out',str(out)]),patch('builtins.print'):
            main()
        s=json.loads((out/'summary.json').read_text())
        self.assertEqual(s['mode'],'offline_baseline_only');self.assertEqual(s['provider_calls'],0)
        self.assertFalse(s['independent_review']);self.assertTrue(s['all_proposals_wait_for_review'])
        self.assertNotIn('model',s['policies']);self.assertIsNone(s['business_accuracy'])

    def test_existing_output_rejected_before_provider_initialization(self):
        with patch('sys.argv',['eval','--live-synthetic','--out',self.tmp.name]):
            with self.assertRaises(FileExistsError):main()

    def test_new_acceptance_does_not_repair_wrong_plan_then_count_completion(self):
        case={'intent':None,'names':NAMES,'profile':'direct'}
        result=conservative_plan('示例甲草和示例乙草的共同靶点加药用部位表')
        task=create_proposal(self.engine,'示例甲草和示例乙草的共同靶点加药用部位表',result)
        assessment=exercise_proposal(self.engine,task,result,case)
        self.assertEqual(assessment['status'],'needs_correction')
        self.assertIn('unsupported_scope_selected',assessment['failure_types'])
        self.assertEqual(self.store.get(task['id']),task);self.assertEqual(task['tool_calls'],0)

    def test_review_execution_allows_common_source_rows_but_checks_selected_studies(self):
        case={'intent':'evidence_review','names':NAMES,'profile':'wrong_object'}
        engine=ResearchTaskEngine(self.store,ScriptedToolModel(),SyntheticResearchTools('wrong_object'))
        result=conservative_plan('示例甲草和示例乙草的研究记录')
        task=create_proposal(engine,'示例甲草和示例乙草的研究记录',result)
        assessment=exercise_proposal(engine,task,result,case)
        self.assertEqual(assessment['status'],'execution_partial');self.assertTrue(all(assessment['checks'].values()))
        self.assertEqual(len(self.store.get(task['id'])['report']['relation_evidence']),2)

    def test_empty_source_execution_and_missing_entities_are_separate_outcomes(self):
        for question,profile,status in [('示例甲草和示例乙草的共同靶点','no_common','execution_complete'),
                                       ('这些对象的共同靶点','direct','clarification_required')]:
            result=conservative_plan(question)
            case={'intent':'source_relations','names':result.plan.names(),'profile':profile}
            engine=ResearchTaskEngine(self.store,ScriptedToolModel(),SyntheticResearchTools(profile))
            task=create_proposal(engine,question,result)
            assessment=exercise_proposal(engine,task,result,case)
            self.assertEqual(assessment['status'],status)


if __name__=='__main__':unittest.main()
