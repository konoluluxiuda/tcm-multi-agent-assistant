"""Known simulated-user failures: fix entry wording without bypassing source guards."""
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.build_mineru_retrieval import build
from tcm.assistant_router import resolve
from tcm.mineru_chat import ResearchTool
from tests.test_mineru_chat import Service
from tests.test_mineru_retrieval import fixture


class NewUserWordingTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        fixture(self.root/'view');build(self.root/'view',self.root/'index');self.service=Service(self.root/'index')
        self.service.names={'示例叶','示例叶炭'}
        env=patch.dict(os.environ,{'ENABLE_NATURAL_ROUTING':'1','RESEARCH_TOOLS_ONLY':'1','ENABLE_EVIDENCE_AUTO_ROUTING':'0'})
        env.start();self.addCleanup(env.stop)

    def test_coverage_does_not_require_selecting_evidence_sources(self):
        for text in ('这里能查到哪些中药资料呀？是药典里的内容，还是也包括论文？',
                     '这个助手能提供什么资料？'):
            self.assertEqual(resolve(text)['request']['topic'],'capabilities')
        self.assertEqual(resolve('黄芪的药典记载和相关文献')['request']['topic'],'clarify_sources')
        self.assertEqual(resolve('这里能查询黄芪的功效资料吗')['tool'],'research')

    def test_polite_field_request_keeps_entity_and_marks_missing_field(self):
        result,_=ResearchTool(self.service).execute('我想看看示例叶炭的用量和注意事项，这里有吗？',None)
        self.assertEqual(result['status'],'partial_evidence')
        self.assertEqual(result['tool_result']['missing_fields'],['注意'])
        self.assertEqual({h['name'] for h in result['tool_result']['hits']},{'示例叶炭'})
        self.assertIn('未检出字段：注意',result['message'])
        self.assertEqual(ResearchTool(self.service).execute('示例叶炭的用量',None)[0]['status'],'candidates')

    def test_politeness_never_removes_preparation_or_longer_unknown_noun(self):
        for text,expected in [('我想看看酒示例叶的用量','variant_scope_unresolved'),
                              ('我想看看假示例叶的用量','name_not_resolved'),
                              ('我想看看示例叶提取物的用量','name_not_resolved')]:
            result,_=ResearchTool(self.service).execute(text,None)
            self.assertEqual(result['status'],expected)
            self.assertFalse((result['tool_result'] or {}).get('hits'))

    def test_introduction_consumes_whole_request_and_preserves_preparation(self):
        text='帮我找一下示例叶，我主要想看药典怎么写的。'
        self.assertEqual(resolve(text)['request'],{'question':'示例叶'})
        self.assertEqual(resolve('帮我找一下酒示例叶，我主要想看药典怎么写的')['request']['question'],'酒示例叶')
        self.assertIsNone(resolve(text+'再推荐给我用'))
        self.assertEqual(resolve('/herb 示例叶')['tool'],'herb')

    def test_missing_and_alias_fields_do_not_create_false_partial(self):
        tool=ResearchTool(self.service)
        self.assertEqual(tool.execute('示例叶炭的注意',None)[0]['status'],'field_not_observed')
        repeated=tool.execute('示例叶的用量和注意',None)[0]
        self.assertEqual(repeated['status'],'ambiguous_field')
        self.assertEqual(repeated['tool_result']['missing_fields'],['注意'])
        self.assertIsNone(tool.execute('示例叶的用量和注意',None)[1])

    def test_real_graph_retains_partial_subject_and_introduction_focus_without_model(self):
        import app
        from agents import agent_decision as ad
        from agents.conversation_store import ConversationStore
        from fastapi.testclient import TestClient
        from langchain_core.runnables import RunnableLambda
        def forbidden(*a,**kw):raise AssertionError('Wording fix called a model')
        with patch.object(ad,'memory',ConversationStore(self.root/'memory.sqlite3')), \
             patch.object(ad.research_runtime,'get_service',return_value=self.service), \
             patch.object(ad.config.agent_decision,'llm',RunnableLambda(forbidden)), \
             patch.object(ad.config.conversation,'llm',RunnableLambda(forbidden)), \
             patch.object(ad.LocalGuardrails,'check_input',forbidden),patch.object(ad.LocalGuardrails,'check_output',forbidden), \
             TestClient(app.app) as client,redirect_stdout(io.StringIO()):
            first=client.post('/chat',json={'query':'我想看看示例叶炭的用量和注意事项，这里有吗？'}).json()
            self.assertEqual(first['status'],'partial');self.assertEqual(first['research']['focus_name'],'示例叶炭')
            source=client.post('/chat',json={'query':'这段是药典原话吗？我能看看它在书里哪一页吗？'}).json()['research']
            self.assertEqual(source['status'],'partial_evidence');self.assertEqual(source['tool_result']['hits'][0]['name'],'示例叶炭')
            intro=client.post('/chat',json={'query':'帮我找一下示例叶，我主要想看药典怎么写的。'}).json()['research']
            self.assertEqual(intro['status'],'field_not_resolved');self.assertEqual(intro['focus_name'],'示例叶')
            self.assertIn(intro['focus_name'],intro['message'])
            follow=client.post('/chat',json={'query':'功效呢'}).json()['research']
            self.assertEqual(follow['status'],'candidates');self.assertEqual(follow['tool_result']['hits'][0]['name'],'示例叶')
