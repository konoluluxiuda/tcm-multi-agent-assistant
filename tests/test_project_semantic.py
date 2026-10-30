import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tcm.project_memory import ProjectStore, ProjectConflict
from tcm.project_semantic import recall, apply_context, memory_reference, document_text


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=ProjectStore(Path(self.temp.name)/'p.db')
        self.a=self.store.save('alice',{'title':'补血活血药比较','herbs':['当归','川芎'],'question':'功效及贮藏'})
        self.b=self.store.save('bob',{'title':'私密项目','herbs':['白术'],'question':'用量'})
        self.names=['当归','川芎','白术','甘草']

    def test_owner_filter_before_embedding(self):
        def ranker(query,docs):
            self.assertEqual([d['id'] for d in docs],[self.a['id']])
            self.assertNotIn('私密',str(docs))
            return {self.a['id']:0.8}
        result=recall(self.store,'alice','继续补血活血比较',ranker=ranker)
        self.assertEqual(result['items'][0]['id'],self.a['id'])

    def test_deleted_during_search_is_discarded(self):
        def ranker(query,docs):
            self.store.delete('alice',self.a['id'],1)
            return {self.a['id']:0.9}
        self.assertEqual(recall(self.store,'alice','继续',ranker=ranker)['items'],[])
        with self.assertRaises(KeyError): apply_context(self.store,'alice',self.a['id'],1,'继续',names=self.names)

    def test_update_during_search_is_discarded(self):
        def ranker(query,docs):
            self.store.save('alice',{'title':'新项目','herbs':['白术'],'question':'用量'},identifier=self.a['id'],revision=1)
            return {self.a['id']:0.9}
        self.assertEqual(recall(self.store,'alice','继续',ranker=ranker)['items'],[])
        with self.assertRaises(ProjectConflict): apply_context(self.store,'alice',self.a['id'],1,'继续',names=self.names)

    def test_threshold_empty_and_unavailable(self):
        self.assertEqual(recall(self.store,'alice','无关',ranker=lambda q,d:{self.a['id']:0.1})['status'],'no_match')
        self.assertEqual(recall(self.store,'nobody','继续',ranker=lambda q,d:self.fail())['status'],'no_memory')
        with patch('tcm.project_semantic.local_rank',side_effect=RuntimeError('offline missing')):
            self.assertEqual(recall(self.store,'alice','继续')['status'],'unavailable')

    def test_current_question_and_object_override(self):
        x=apply_context(self.store,'alice',self.a['id'],1,'继续上次的研究，这次只看贮藏',names=self.names)
        self.assertEqual(x['herbs'],['当归','川芎']);self.assertEqual(x['question'],'看贮藏')
        x=apply_context(self.store,'alice',self.a['id'],1,'继续上次的研究，这次看白术的贮藏',names=self.names)
        self.assertEqual(x['herbs'],['白术']);self.assertNotIn('功效',x['question'])
        x=apply_context(self.store,'alice',self.a['id'],1,'继续上次那组补血活血药的比较',names=self.names)
        self.assertEqual(x['question'],'功效及贮藏')

    def test_unknown_replacement_and_extra_objects(self):
        for query in ['继续上次，这次换成未知药','继续上次，这次看当归川芎白术']:
            with self.assertRaises(ProjectConflict):apply_context(self.store,'alice',self.a['id'],1,query,names=self.names)
        with self.assertRaises(KeyError):apply_context(self.store,'bob',self.a['id'],1,'继续',names=self.names)

    def test_unknown_requirements_are_not_dropped(self):
        query='继续上次的研究，这次预测它治疗疾病的效果'
        x=apply_context(self.store,'alice',self.a['id'],1,query,names=self.names)
        self.assertIn('预测',x['question']);self.assertNotEqual(x['question'],self.a['question'])

    def test_short_term_and_ordinary_queries_not_intercepted(self):
        for q in ['白术的功效','刚才那味药的用量','它的出处呢']:
            self.assertFalse(memory_reference(q))
        self.assertTrue(memory_reference('继续上次那组补血活血药的比较'))

    def test_main_chat_uses_only_local_memory(self):
        from fastapi.testclient import TestClient
        import app
        from agents.conversation_store import ConversationStore
        memory=ConversationStore(Path(self.temp.name)/'chat.db')
        with patch.dict(os.environ,{'ENABLE_RESEARCH_TASKS':'1','ETCM_DISEASE_DB':'fixture','PROJECT_MEMORY_PYTHON':'local','PROJECT_MEMORY_MODEL':'local'}), \
             patch.object(app.agent_runtime,'memory',memory), \
             patch('tcm.project_semantic.recall',return_value={'status':'candidates','items':[],'external_calls':0}) as search, \
             patch.object(app,'process_query',side_effect=AssertionError('Provider path used')):
            with TestClient(app.app) as client:
                response=client.post('/chat',json={'query':'继续上次的研究'})
                self.assertEqual(response.status_code,200,response.text)
                self.assertIn('project_memory',response.json());search.assert_called_once()
                self.assertIn(app.SESSION_COOKIE_NAME,client.cookies)

    def test_worker_timeout_and_invalid_output_do_not_fallback(self):
        import subprocess
        from types import SimpleNamespace
        from tcm.project_semantic import local_rank
        env={'PROJECT_MEMORY_PYTHON':'/local/python','PROJECT_MEMORY_MODEL':'/local/model','CHAT_API_KEY':'not-for-child'}
        with patch.dict(os.environ,env),patch('tcm.project_semantic.subprocess.run') as worker:
            worker.side_effect=subprocess.TimeoutExpired('local',25)
            self.assertEqual(recall(self.store,'alice','继续')['status'],'unavailable')
            self.assertNotIn('CHAT_API_KEY',worker.call_args.kwargs['env'])
            worker.side_effect=None
            worker.return_value=SimpleNamespace(stdout='{"scores":{"another_owner":0.99},"truncated":false}')
            self.assertEqual(recall(self.store,'alice','继续')['status'],'unavailable')
            worker.return_value=SimpleNamespace(stdout='{"scores":{},"truncated":true}')
            self.assertEqual(recall(self.store,'alice','继续')['status'],'unavailable')

    def test_recall_apply_api_revalidates_owner_and_revision(self):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from tcm.project_memory_api import create_router
        class Memory:
            path=Path(self.temp.name)/'chat.db'
            def valid_cookie(self,c):return c in ('alice','bob')
        app=FastAPI();app.include_router(create_router(Memory,lambda:'chat'))
        with patch.dict(os.environ,{'ENABLE_RESEARCH_TASKS':'1','ETCM_DISEASE_DB':'fixture','RESEARCH_PROJECT_DB':str(Path(self.temp.name)/'api.db')}), \
             patch('tcm.project_semantic.local_rank',side_effect=lambda q,docs:{d['id']:0.8 for d in docs}), \
             patch('tcm.project_semantic.local_names',return_value=self.names):
            with TestClient(app) as client:
                client.cookies.set('chat','alice')
                doc=client.post('/api/research/projects',json={'title':'研究','herbs':['当归'],'question':'功效'}).json()
                data=client.post('/api/research/projects/recall',json={'query':'继续上次研究'}).json()
                self.assertEqual(data['items'][0]['id'],doc['id'])
                url='/api/research/projects/'+doc['id']+'/apply'
                body={'revision':1,'query':'继续上次研究，这次看白术贮藏'}
                self.assertEqual(client.post(url,json=body).json()['herbs'],['白术'])
                self.assertEqual(client.post(url,json={**body,'revision':2}).status_code,409)
                self.assertEqual(client.post(url,json=body,headers={'Origin':'https://other.example'}).status_code,403)
                client.cookies.set('chat','bob')
                self.assertEqual(client.post(url,json=body).status_code,404)
                self.assertEqual(client.post('/api/research/projects/recall',json={'query':'继续'}).json()['status'],'no_memory')

    def test_multiple_current_markers_keep_all_requirements(self):
        query='继续上次的研究，这次只看白术的贮藏，现在还要注意事项'
        x=apply_context(self.store,'alice',self.a['id'],1,query,names=self.names)
        self.assertEqual(x['herbs'],['白术'])
        self.assertEqual(x['question'],'看白术的贮藏，现在还要注意事项')
        self.assertEqual(x['original_query'],query)

    def test_excluded_herb_does_not_become_report_subject(self):
        for exclusion in ['不要当归','不用当归','不查当归','不看当归','排除当归','去掉当归','不含当归']:
            with self.subTest(exclusion=exclusion),self.assertRaises(ProjectConflict):
                apply_context(self.store,'alice',self.a['id'],1,'继续上次的研究，这次'+exclusion+'，只看白术的贮藏',names=self.names)

    def test_natural_project_references_and_query_boundaries(self):
        for query in ['以前那份白术配茯苓的资料在哪里','丹参和鸡血藤那份笔记继续做',
                      '当归、川芎的资料还没整理完','那份关于调理脾胃的药材资料',
                      '补血那两味究竟是哪一组','当归川芎这组，这次只关心保存要求',
                      '上次讨论过的所有药材项目']:
            with self.subTest(query=query):self.assertTrue(memory_reference(query))
        for query in ['白术相关属性','有哪些药材','列出所有靶点','刚才那味药的用量',
                      '不要找历史项目，只查白术功效','介绍项目使用的数据库']:
            with self.subTest(query=query):self.assertFalse(memory_reference(query))

    def test_explicit_unsaved_herb_does_not_encode_other_projects(self):
        result=recall(self.store,'alice','找之前黄芪的研究项目',names=self.names+['黄芪'],
                      ranker=lambda q,d:self.fail('Unsaved object must not trigger encoding'))
        self.assertEqual(result['reason'],'explicit_objects_not_saved')
        self.assertEqual(result['items'],[])

    def test_recall_old_object_then_apply_new_object(self):
        self.store.save('alice',{'title':'白术项目','herbs':['白术'],'question':'功效'})
        def ranker(query,docs):
            self.assertEqual(query,'继续当归项目')
            self.assertEqual([d['id'] for d in docs],[self.a['id']])
            return {self.a['id']:0.8}
        query='继续当归项目，这次看白术的贮藏'
        result=recall(self.store,'alice',query,ranker=ranker,names=self.names)
        self.assertEqual(result['query'],query)
        applied=apply_context(self.store,'alice',result['items'][0]['id'],1,query,names=self.names)
        self.assertEqual(applied['herbs'],['白术'])

    def test_list_returns_all_owner_projects_without_embedding_and_preserves_focus(self):
        for i in range(4):
            self.store.save('alice',{'title':f'项目{i}','herbs':['白术'],'question':'贮藏'})
        query='列出所有药材项目'
        result=recall(self.store,'alice',query,names=self.names,ranker=lambda q,d:self.fail('List must not encode'))
        self.assertEqual(result['total'],5)
        self.assertNotIn(self.b['id'],[d['id'] for d in result['items']])
        applied=apply_context(self.store,'alice',self.a['id'],1,query,names=self.names)
        self.assertEqual(applied['question'],self.a['question'])
        self.assertEqual(applied['herbs'],self.a['herbs'])
        filtered=recall(self.store,'alice','列出所有当归项目',names=self.names)
        self.assertEqual(filtered['total'],1)
        applied=apply_context(self.store,'alice',self.a['id'],1,filtered['query'],names=self.names)
        self.assertEqual(applied['herbs'],self.a['herbs'])

    def test_listing_chat_works_without_embedding_configuration(self):
        from fastapi.testclient import TestClient
        import app
        from agents.conversation_store import ConversationStore
        memory=ConversationStore(Path(self.temp.name)/'list-chat.db')
        with patch.dict(os.environ,{'ENABLE_RESEARCH_TASKS':'1','ETCM_DISEASE_DB':'fixture','PROJECT_MEMORY_PYTHON':'','PROJECT_MEMORY_MODEL':''}), \
             patch.object(app.agent_runtime,'memory',memory), \
             patch('tcm.project_semantic.recall',return_value={'status':'candidates','method':'project_list','total':4,'items':[],'external_calls':0}), \
             patch.object(app,'process_query',side_effect=AssertionError('Provider path used')):
            with TestClient(app.app) as client:
                response=client.post('/chat',json={'query':'列出所有研究项目'})
                self.assertEqual(response.status_code,200,response.text)
                self.assertIn('4 个',response.json()['response'])
