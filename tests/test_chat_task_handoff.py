"""Simple queries run directly; explicit tasks retain the reviewed workflow."""
from contextlib import redirect_stdout
import io
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.runnables import RunnableLambda
from agents.conversation_store import ConversationStore
from tests import test_relation_queries as fixtures
from tcm.assistant_info import execute
from tcm.assistant_router import resolve
from tcm.task_tools import LocalTaskTools

DIRECT_QUESTION='我在整理当归与川芎，想核对共同靶点，并保留双方原始记录的位置'
QUESTION='请为当归与川芎创建共同靶点研究任务，并保留双方原始记录的位置'
FIXTURE_QUESTION='请为测试甲草与测试乙草创建共同靶点研究任务，并保留双方原始记录的位置'


class ChatTaskHandoffTests(unittest.TestCase):
    def setUp(self):
        fixtures.RelationQueryTests.setUp(self)
        import app
        from agents import agent_decision as ad
        self.app=app;self.ad=ad;self.task_db=self.root/'tasks.sqlite3'
        self.memory=ConversationStore(self.root/'chat.sqlite3')
        def forbidden(*args,**kwargs):raise AssertionError('Handoff reached a model or guardrail')
        patches=[patch.dict(os.environ,{'ENABLE_RESEARCH_TASKS':'1','RESEARCH_TASK_DB':str(self.task_db),
                    'RESEARCH_TOOLS_ONLY':'1','ENABLE_NATURAL_ROUTING':'1',
                    'ENABLE_EVIDENCE_ASSISTANT':'0','ENABLE_EVIDENCE_AUTO_ROUTING':'0'}),
                 patch.object(ad,'memory',self.memory),
                 patch.object(ad.config.agent_decision,'llm',RunnableLambda(forbidden)),
                 patch.object(ad.config.conversation,'llm',RunnableLambda(forbidden)),
                 patch.object(ad.LocalGuardrails,'check_input',forbidden),
                 patch.object(ad.LocalGuardrails,'check_output',forbidden),
                 patch('tcm.research_task.NativeToolModel.__init__',side_effect=forbidden)]
        for item in patches:item.start();self.addCleanup(item.stop)
        self.client=TestClient(app.app);self.other=TestClient(app.app)
        self.addCleanup(self.client.close);self.addCleanup(self.other.close)

    def turn(self,question=QUESTION,mode='auto'):
        with redirect_stdout(io.StringIO()):return self.client.post('/chat',json={'query':question,'answer_mode':mode})

    def test_explicit_task_and_other_compound_requirements_are_preserved(self):
        for text in (QUESTION,'  '+QUESTION+'  ',
                     '我在整理测试甲草、测试乙草和未知第三草的共同靶点，还要药用部位表和论文。'):
            selected=resolve(text)
            self.assertEqual(selected['reason'],'local_task_inventory_review')
            self.assertEqual(selected['request'],{'topic':'research_task_review','question':text})
            result=execute(selected['request'])
            self.assertTrue(result['can_create_task'])
            self.assertEqual(result['task_proposal'],{'mode':'etcm_question','question':text})
            self.assertEqual(result['status'],'clarification_needed')

    def test_existing_direct_commands_routes_and_personal_boundary_keep_precedence(self):
        self.assertEqual(resolve('测试甲草和测试乙草有哪些共同靶点')['reason'],'etcm_relation_query')
        self.assertEqual(resolve('/relations 共同靶点 Alpha | Beta')['mode'],'explicit')
        self.assertEqual(resolve('我想查共同靶点，再帮我配药')['request']['topic'],'personal_medication_boundary')
        self.assertIsNone(resolve('/unknown '+QUESTION))
        with patch.dict(os.environ,{'ENABLE_NATURAL_ROUTING':'0'}):self.assertIsNone(resolve(QUESTION))

    def test_real_chat_graph_returns_review_action_before_any_task_or_relation_tools(self):
        with patch.object(LocalTaskTools,'invoke',side_effect=AssertionError('No relation tools')):
            response=self.turn();self.assertEqual(response.status_code,200,response.text)
            result=response.json();self.assertEqual(result['agent'],'ASSISTANT_INFO_AGENT')
            self.assertEqual(result['assistant']['topic'],'research_task_review')
            self.assertEqual(result['assistant']['task_proposal']['question'],QUESTION)
            self.assertEqual(result['assistant']['llm_calls'],0)
            self.assertEqual(result['assistant']['network_calls'],0)
            self.assertEqual(result['routing']['reason'],'local_task_inventory_review')
            self.assertFalse(self.task_db.exists())
            self.assertEqual(self.client.get('/api/research/tasks').json()['items'],[])

    def test_explicit_evidence_toggle_cannot_send_handoff_to_model(self):
        for enabled in ('0','1'):
            with patch.dict(os.environ,{'ENABLE_EVIDENCE_ASSISTANT':enabled}), \
                 patch('tcm.evidence_assistant.configured_assistant',side_effect=AssertionError('No evidence model')):
                response=self.turn(mode='evidence');self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()['assistant']['llm_calls'],0)
                self.assertNotIn('evidence_answer',response.json())

    def test_feature_off_has_specific_guidance_and_no_dead_action(self):
        with patch.dict(os.environ,{'ENABLE_RESEARCH_TASKS':'0'}):
            response=self.turn();self.assertEqual(response.status_code,200,response.text)
            result=response.json()['assistant'];self.assertFalse(result['can_create_task'])
            self.assertNotIn('task_proposal',result);self.assertIn('尚未启用',result['message'])
            self.assertFalse(self.task_db.exists())

    def test_overlong_question_is_not_silently_truncated_or_proposed(self):
        text=QUESTION+'额外要求'*100
        self.assertGreater(len(text),400)
        response=self.turn(text);self.assertEqual(response.status_code,200,response.text)
        result=response.json()['assistant'];self.assertFalse(result['can_create_task'])
        self.assertNotIn('task_proposal',result);self.assertIn('400',result['message'])
        self.assertFalse(self.task_db.exists())

    def test_clicked_proposal_enters_existing_review_and_full_local_flow(self):
        result=self.turn(FIXTURE_QUESTION).json()['assistant'];proposal=result['task_proposal']
        response=self.client.post('/api/research/tasks',json=proposal)
        self.assertEqual(response.status_code,200,response.text);task=response.json();url='/api/research/tasks/'+task['id']
        self.assertEqual(task['status'],'waiting_request');self.assertEqual(task['question'],FIXTURE_QUESTION)
        self.assertEqual(task['tool_calls'],0);self.assertEqual(task['external_model_calls'],0)
        before=task
        self.assertEqual(self.client.post(url+'/choose',json={'revision':task['revision'],'selection':{}}).status_code,422)
        self.assertEqual(self.other.get(url).status_code,404)
        self.assertEqual(self.client.get(url).json(),before)
        response=self.client.post(url+'/request',json={'revision':task['revision'],
            'request_scope':{'data_mode':'etcm_local','entities':['测试甲草','测试乙草'],'deliverables':['source_relations']},'confirm_complete':True})
        self.assertEqual(response.status_code,200,response.text);task=response.json()
        response=self.client.post(url+'/choose',json={'revision':task['revision'],
            'selection':{'intent':'source_relations','herb_one':'测试甲草','herb_two':'测试乙草'}})
        self.assertEqual(response.status_code,200,response.text);task=response.json()
        sources={g['name']:g['options'][0]['id'] for g in task['pending']['options']}
        task=self.client.post(url+'/choose',json={'revision':task['revision'],'selection':sources}).json()
        task=self.client.post(url+'/choose',json={'revision':task['revision'],'selection':[task['pending']['options'][0]['id']]}).json()
        self.assertEqual(task['status'],'complete');self.assertTrue(task['report']['original_request_complete'])
        self.assertEqual(task['report']['common_target_total'],23)
        self.assertIn(FIXTURE_QUESTION,self.client.get(url+'/report').text)

    def test_unknown_unrelated_questions_still_do_not_trigger_tasks_or_models(self):
        response=self.turn('完全不属于已启用资料工具的临时测试问题')
        self.assertEqual(response.status_code,403);self.assertFalse(self.task_db.exists())

    def test_simple_background_questions_return_results_without_review_or_task_creation(self):
        original=resolve(DIRECT_QUESTION)
        self.assertEqual(original['request']['entities'],['当归','川芎'])
        for text in (
                '我在整理测试甲草与测试乙草，想核对共同靶点',
                '我在整理测试甲草与测试乙草，想核对共同靶点，并保留双方原始记录的位置',
                '我正在比较测试乙草和测试甲草，希望查询双方的共同标识，附上双方原始行',
                '核对测试甲草与测试乙草共同靶点，给出双方出处',
                '测试甲草和测试乙草有哪些共同靶点'):
            with self.subTest(text=text),patch.object(LocalTaskTools,'invoke',side_effect=AssertionError('No task engine')):
                response=self.turn(text);self.assertEqual(response.status_code,200,response.text)
                data=response.json();result=data['assistant']
                self.assertEqual(result['topic'],'etcm_relations');self.assertEqual(result['status'],'ok')
                self.assertEqual(result['relations']['data']['total'],23)
                self.assertEqual([g['selection_method'] for g in result['relations']['groups']],['unique_local_candidate']*2)
                self.assertNotIn('task_proposal',result);self.assertNotIn('核对请求清单',data['response'])
                for group in result['relations']['groups']:
                    self.assertIn('relative_file_path',group['options'][0]['source'])
                for source in result['relations']['data']['items'][0]['sources']:
                    self.assertTrue(source['witnesses'][0]['source_pointer'].startswith('/data/'))
                self.assertFalse(self.task_db.exists())

    def test_simple_query_replaces_pending_review_and_stays_local_even_with_ai_toggle(self):
        self.turn();self.assertIn('task_proposal',self.client.get('/conversation').json()['assistant'])
        with patch.dict(os.environ,{'ENABLE_EVIDENCE_ASSISTANT':'1'}), \
             patch('tcm.evidence_assistant.configured_assistant',side_effect=AssertionError('No evidence model')):
            response=self.turn('我在整理测试甲草与测试乙草，想核对共同靶点',mode='evidence')
        self.assertEqual(response.status_code,200,response.text)
        result=response.json();self.assertEqual(result['assistant']['relations']['data']['total'],23)
        self.assertEqual(result['assistant']['llm_calls'],0);self.assertNotIn('evidence_answer',result)
        history=self.client.get('/conversation').json()
        self.assertEqual(history['assistant']['relations']['data']['total'],23)
        self.assertNotIn('task_proposal',history['assistant']);self.assertFalse(self.task_db.exists())

    def test_explicit_uniprot_intersection_enters_review_and_has_a_goal_anchor(self):
        question='测试甲草、测试乙草这两份来源都提到了哪些uniprot标识？先列交集，再给我出处。'
        response=self.turn(question);self.assertEqual(response.status_code,200,response.text)
        proposal=response.json()['assistant']['task_proposal']
        self.assertEqual(proposal['question'],question)
        task=self.client.post('/api/research/tasks',json=proposal).json()
        self.assertEqual(task['status'],'waiting_request');self.assertEqual(task['tool_calls'],0)
        self.assertEqual(task['request_draft']['deliverables'],['source_relations'])
        anchors=[a for a in task['request_draft']['anchors'] if a['kind']=='deliverable']
        self.assertEqual(len(anchors),1)
        self.assertEqual(question[anchors[0]['start']:anchors[0]['end']],'交集')
        self.assertNotEqual(resolve('请查询这两个文献集合的交集')['request']['topic'],'research_task_review')

    def test_pending_review_survives_memory_reopen_without_get_mutations_or_tools(self):
        self.turn();cookie=self.client.cookies.get(self.app.SESSION_COOKIE_NAME)
        before=self.memory.load_serialized(cookie)
        with patch.object(self.ad,'memory',ConversationStore(self.memory.path)), \
             patch.object(LocalTaskTools,'invoke',side_effect=AssertionError('GET ran a task tool')):
            for _ in range(2):
                response=self.client.get('/conversation');self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()['assistant']['task_proposal']['question'],QUESTION)
        self.assertEqual(self.memory.load_serialized(cookie),before)
        self.assertFalse(self.task_db.exists())
        self.assertNotIn('assistant',self.other.get('/conversation').json())

    def test_new_successful_turn_or_chat_clear_removes_old_pending_action(self):
        self.turn();self.assertIn('assistant',self.client.get('/conversation').json())
        self.turn('你能做什么');self.assertNotIn('assistant',self.client.get('/conversation').json())
        self.turn();self.assertIn('assistant',self.client.get('/conversation').json())
        self.client.delete('/conversation');history=self.client.get('/conversation').json()
        self.assertEqual(history['messages'],[]);self.assertNotIn('assistant',history)

    def test_new_review_does_not_restore_previous_source_as_its_evidence(self):
        data=self.turn('测试甲草和测试乙草有哪些共同靶点').json()
        ids=[g['options'][0]['source_record_id'] for g in data['assistant']['relations']['groups']]
        self.turn('/relations 共同靶点 '+' | '.join(ids))
        cookie=self.client.cookies.get(self.app.SESSION_COOKIE_NAME)
        self.assertIsNotNone(self.memory.load_serialized(cookie).tcm_context['relation'])
        self.turn();context=self.memory.load_serialized(cookie).tcm_context
        for key in ('herb','research','pubmed','relation'):self.assertIsNone(context[key])
        with patch('tcm.relation_memory.execute_turn',side_effect=AssertionError('Old source replayed')):
            restored=self.client.get('/conversation').json()
        self.assertEqual(restored['assistant']['topic'],'research_task_review')
        self.assertEqual(restored['assistant']['task_proposal']['question'],QUESTION)


if __name__=='__main__':unittest.main()
