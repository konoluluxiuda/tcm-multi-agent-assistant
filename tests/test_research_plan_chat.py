"""A chat integration with real synthetic catalogs, local workers and SDK substitutes."""
from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from langchain_core.runnables import RunnableLambda
from agents.conversation_store import ConversationStore
from tests.research_business_fixture import BusinessFixture
from tests import test_intent_planner as intents
from tcm.research_plan_intent import local_plan,validate_question,capture
from tcm.intent_planner import plan_question

QUESTION='查找Synthetic disease的来源，读取全部靶点原始记录，再按Jaccard列出前五个药材候选，保留出处'


class PlanTests(unittest.TestCase):
    def test_complete_local_plan_and_modifiers(self):
        plan=local_plan(QUESTION)
        self.assertEqual([s.call.tool for s in plan.steps],['search_disease_sources','disease_gene_profile','rank_herbs_for_gene_set'])
        self.assertTrue(plan.steps[1].all_pages);self.assertEqual(plan.steps[2].call.arguments.top_k,5)
        herbs=local_plan('Alpha的靶点画像，查找与Beta靶点最相似的药材，前5个')
        self.assertEqual(herbs.steps[1].call.arguments.top_k,5)
        for q in (QUESTION+'，再查采购价格',QUESTION.replace('五','三十'),'Alpha的靶点画像，给我配药'):
            self.assertIsNone(local_plan(q))

    def test_model_plan_question_only_and_lost_requirements_are_blocked(self):
        q='先描一下Alpha的靶点盘子，再看看Beta有哪些靶点'
        plan={'steps':[{'call':{'tool':'herb_target_profile','arguments':{'name':name}},'clauses':[i]} for i,name in enumerate(('Alpha','Beta'))]}
        model=intents.Model({'action':'analysis_plan','plan':plan})
        selected=plan_question(q,sdk=model)
        self.assertEqual(selected['intent_status'],'ok',selected)
        self.assertEqual(selected['request']['topic'],'research_plan')
        self.assertEqual(len(model.inputs),1);self.assertEqual(len(model.inputs[0]),2)
        self.assertEqual(model.inputs[0][1].content,q)
        for wrong in (copy.deepcopy(plan),copy.deepcopy(plan)):
            wrong['steps'][1]['clauses']=[0]
            self.assertEqual(plan_question(q,sdk=intents.Model({'action':'analysis_plan','plan':wrong}))['intent_status'],'invalid_output')
        full=local_plan(QUESTION).model_dump()
        for change in ('full','count','name','candidate','metric'):
            wrong=copy.deepcopy(full)
            if change=='full':wrong['steps'][1]['all_pages']=False
            elif change=='count':wrong['steps'][2]['call']['arguments']['top_k']=3
            elif change=='name':wrong['steps'][0]['call']['arguments']['name']='Invented disease'
            elif change=='metric':wrong['steps'][2]['call']['arguments']['metric']='common_count'
            else:wrong['steps'][2]['call']={'tool':'herb_target_profile','arguments':{'name':'Synthetic disease'}}
            with self.assertRaises(ValueError):validate_question(wrong,QUESTION)


    def test_model_cannot_borrow_another_clause_object_or_page(self):
        q='Alpha的靶点画像，Beta的靶点画像'
        plan=local_plan(q).model_dump()
        plan['steps'][1]['call']['arguments']['name']='Alpha'
        with self.assertRaises(ValueError):validate_question(plan,q)
        disease=local_plan(QUESTION).model_dump()
        disease['steps'][1]['all_pages']=False
        disease['steps'][1]['call']['arguments']['page']=3
        with self.assertRaises(ValueError):validate_question(disease,QUESTION)



class ChatTests(unittest.TestCase):
    def setUp(self):
        self.fixture=BusinessFixture();self.f=self.fixture.__enter__();self.addCleanup(self.fixture.__exit__,None,None,None)
        import app
        from agents import agent_decision as ad
        self.app=app;self.ad=ad;self.memory=ConversationStore(self.f.root/'chat.sqlite3')
        self.audit=self.f.root/'runs.sqlite3'
        def forbidden(*a,**kw):raise AssertionError('Unexpected legacy model or guardrail')
        for p in [patch.dict(os.environ,{'ENABLE_RESEARCH_TASKS':'1','ENABLE_NATURAL_ROUTING':'1',
            'ENABLE_INTENT_PLANNER':'1','RESEARCH_TOOLS_ONLY':'1','ENABLE_EVIDENCE_ASSISTANT':'0',
            'ENABLE_EVIDENCE_AUTO_ROUTING':'0','RESEARCH_RUN_DB':str(self.audit)}),
            patch.object(ad,'memory',self.memory),patch.object(ad.config.agent_decision,'llm',RunnableLambda(forbidden)),
            patch.object(ad.config.conversation,'llm',RunnableLambda(forbidden)),
            patch.object(ad.LocalGuardrails,'check_input',forbidden),patch.object(ad.LocalGuardrails,'check_output',forbidden)]:
            p.start();self.addCleanup(p.stop)
        self.client=TestClient(app.app);self.addCleanup(self.client.close)

    def turn(self,q,**extra):
        with redirect_stdout(io.StringIO()):return self.client.post('/chat',json={'query':q,**extra})

    def test_disease_full_workflow_no_model_or_manual_review_and_reopen(self):
        with patch('tcm.intent_planner.plan_question',side_effect=AssertionError('Complete local plan called model')):
            response=self.turn(QUESTION,answer_mode='evidence')
        self.assertEqual(response.status_code,200,response.text);data=response.json()
        self.assertEqual(data['assistant']['status'],'ok',data)
        self.assertEqual(data['assistant']['execution']['tool_calls'],4)
        rows=data['assistant']['blocks'][0]['rows']
        self.assertEqual(len(rows),24);self.assertNotIn('核对请求清单',data['response'])
        self.assertEqual(len(data['assistant']['blocks'][1]['items']),2)
        self.assertEqual(data['routing']['reason'],'multi_step_analysis')
        self.assertEqual(self.client.get('/conversation').json()['assistant']['run_id'],data['assistant']['run_id'])
        replay=self.turn('出处呢').json()['assistant']
        self.assertEqual(replay['run_id'],data['assistant']['run_id'])
        cookie=self.client.cookies[self.app.SESSION_COOKIE_NAME]
        saved=self.memory.load(cookie)
        self.assertNotIn('raw_row',str(saved.tcm_context));self.assertNotIn('P00000',str(saved.tcm_context))
        other=TestClient(self.app.app);self.addCleanup(other.close)
        self.assertEqual(other.get('/api/research/runs/'+data['assistant']['run_id']).status_code,401)
        other.get('/conversation')
        self.assertEqual(other.get('/api/research/runs/'+data['assistant']['run_id']).status_code,404)

    def test_herb_only_plan_runs_without_disease_catalog(self):
        with patch.dict(os.environ,{'ETCM_DISEASE_DB':''}),patch('tcm.intent_planner.plan_question',side_effect=AssertionError('No model')):
            r=self.turn('Alpha的靶点画像，查找与Beta靶点最相似的药材，前5个')
        self.assertEqual(r.status_code,200,r.text)
        result=r.json()['assistant'];self.assertEqual(result['status'],'ok',result)
        self.assertEqual([b['tool'] for b in result['blocks']],['herb_target_profile','herb_target_similarity'])

    def test_one_question_only_model_proposal_uses_local_sources(self):
        q='先描一下Alpha的靶点盘子，再看看Beta有哪些靶点'
        model=intents.Model({'action':'analysis_plan','plan':{'steps':[
            {'call':{'tool':'herb_target_profile','arguments':{'name':name}},'clauses':[i]} for i,name in enumerate(('Alpha','Beta'))]}})
        self.turn('介绍两个数据库')
        with patch('config.create_chat_model',return_value=model):r=self.turn(q)
        self.assertEqual(r.status_code,200,r.text);data=r.json()
        self.assertEqual(data['model_calls'],1);self.assertEqual(data['assistant']['status'],'ok',data)
        self.assertEqual(len(model.inputs),1);self.assertEqual(len(model.inputs[0]),2)
        self.assertEqual(model.inputs[0][1].content,q)
        self.assertNotIn('P00000',str(model.inputs));self.assertNotIn('介绍两个数据库',str(model.inputs))

    def test_model_cannot_downgrade_compound_to_single_query(self):
        model=intents.Model({'action':'herb_profile','entities':['Alpha']})
        with patch('config.create_chat_model',return_value=model),patch('tcm.research_plan_run.run_local_actions',side_effect=AssertionError('Must not execute')):
            r=self.turn('先描一下Alpha的靶点盘子，再看看Beta有哪些靶点')
        self.assertEqual(r.json()['assistant']['topic'],'intent_clarification')
        self.assertEqual(len(model.inputs),1)

    def test_ambiguous_herb_selection_retains_entire_question_and_blocks_other_session(self):
        with sqlite3.connect(self.f.herbs.database) as db:
            db.execute("INSERT INTO name_candidates SELECT herb_id,'测试甲草',entry_id,evidence FROM name_candidates WHERE name_zh='测试乙草'")
        q='测试甲草的靶点画像，Beta的靶点画像'
        r=self.turn(q).json()['assistant']
        self.assertEqual(r['status'],'clarification_needed');self.assertEqual(len(r['options']),2)
        self.assertEqual(len(self.client.get('/conversation').json()['assistant']['options']),2)
        command=r['options'][0]['query']
        other=TestClient(self.app.app);self.addCleanup(other.close)
        with redirect_stdout(io.StringIO()):bad=other.post('/chat',json={'query':command}).json()['assistant']
        self.assertEqual(bad['status'],'clarification_needed')
        result=self.turn(command).json()['assistant']
        self.assertEqual(result['status'],'ok',result);self.assertEqual(len(result['blocks']),2)
        with sqlite3.connect(self.audit) as db:
            stored=json.loads(db.execute('SELECT document FROM run_journal').fetchone()[0])
        self.assertEqual(stored['question'],q)

    def test_disease_prefix_selection_reexecutes_full_requested_scope(self):
        q=QUESTION.replace('Synthetic disease','Synthetic')
        r=self.turn(q).json()['assistant'];self.assertEqual(r['status'],'partial',r)
        self.assertTrue(r['options'])
        result=self.turn(r['options'][0]['query']).json()['assistant']
        self.assertEqual(result['status'],'ok',result)
        self.assertEqual(len(result['blocks'][0]['rows']),24)
        self.assertEqual(len(result['blocks'][1]['items']),2)

    def test_source_changes_invalidate_replay_and_pending_selection(self):
        r=self.turn(QUESTION).json()['assistant']
        with sqlite3.connect(self.f.diseases) as db:db.execute("UPDATE metadata SET value='\"changed\"' WHERE key='source_fingerprint'")
        restored=self.client.get('/conversation').json()['assistant']
        self.assertEqual(restored['status'],'unavailable')

    def test_unhandled_clause_or_disabled_model_never_executes_partial_plan(self):
        with patch.dict(os.environ,{'ENABLE_INTENT_PLANNER':'0'}),patch('tcm.research_plan_run.run_local_actions',side_effect=AssertionError('Incomplete plan executed')):
            r=self.turn(QUESTION+'，再写一首诗')
        self.assertNotEqual(r.json()['assistant']['status'],'ok')
        with patch('tcm.research_plan_run.run_local_actions',side_effect=AssertionError('Price plan executed')):
            r=self.turn(QUESTION+'，再查采购价格')
        self.assertNotEqual(r.json()['assistant'].get('topic'),'research_plan')
