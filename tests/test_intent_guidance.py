"""Known vague requests get bounded guidance; no fabricated selections or hidden tools."""
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tcm.assistant_router import resolve
from tcm.assistant_info import execute
from tcm.intent_guidance import recognize


class IntentGuidanceTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ,{'ENABLE_NATURAL_ROUTING':'1','RESEARCH_TOOLS_ONLY':'1','ENABLE_PUBMED':'0','ENABLE_EVIDENCE_AUTO_ROUTING':'0'})
        env.start();self.addCleanup(env.stop)

    def test_personal_selection_has_explicit_boundary_without_capturing_source_selection(self):
        text='我最近总觉得累，能不能根据这些资料帮我选两味药配着喝？'
        request=resolve(text)['request'];self.assertEqual(request['topic'],'personal_medication_boundary')
        self.assertIn('不能根据你的症状选药',execute(request)['message'])
        for text in ('帮我选择药材来源','我想查配药研究的相关文献','查询黄芪的用量'):
            self.assertIsNone(recognize(text))
        self.assertEqual(resolve('/research 黄芪的功效')['tool'],'research')

    def test_candidate_guidance_does_not_invent_or_select_records(self):
        request=resolve('我搜丹参怎么出来几个选项？看起来都是丹参，我该选哪个？')['request']
        result=execute(request);self.assertEqual(request['topic'],'source_selection_help')
        self.assertIn('药用部位',result['message']);self.assertNotIn('records',result)
        self.assertEqual(result['status'],'clarification_needed')

    def test_evidence_meaning_is_not_experimental_verification(self):
        request=resolve('这里列的这些成分，是说药材里实际检测到过，还是数据库根据别的资料收进去的？')['request']
        self.assertEqual(request['topic'],'association_evidence_scope')
        result=execute(request);self.assertIn('没有核验',result['message']);self.assertIn('JSON位置',result['message'])
        self.assertNotIn('relations',result)

    def test_provenance_only_replays_relevant_selected_focus(self):
        text='丹参这个成分旁边列了个靶点，这个关联是从哪儿来的？能点回原来的记录吗？'
        self.assertEqual(resolve(text)['request']['topic'],'relation_source_needed')
        for kind in ('disease','ingredient'):
            self.assertEqual(resolve(text,relation_context={'query_kind':kind})['request']['topic'],'relation_source_needed')
        for kind in ('herb_target_paths','target_ingredient'):
            # An explicitly named herb is not silently mapped onto the old focus.
            self.assertEqual(resolve(text,relation_context={'query_kind':kind})['request']['topic'],'relation_source_needed')
            request=resolve('这个成分对应靶点的出处在哪？',relation_context={'query_kind':kind})['request']
            self.assertEqual((request['topic'],request['action']),('etcm_relations','replay'))

    def test_named_relation_plus_provenance_queries_the_full_named_scope(self):
        questions = [
            ('白术的成分关联哪些靶点，请把对应的原始记录也列出来。', 'herb_target_paths', ['白术'], 1),
            ('黄芪的成分对应哪些靶点，同时附上原始行与出处。', 'herb_target_paths', ['黄芪'], 1),
            ('CYP24A1有哪些成分 第2页，给出对应的原始记录。', 'target_ingredient', ['CYP24A1'], 2),
            ('CYP24A1关联哪些药材，附上出处。', 'target_herb', ['CYP24A1'], 1),
            ('白术和黄芪有哪些共同靶点，列出每条关联的原始行和出处。', 'common_target', ['白术','黄芪'], 1),
        ]
        for question,kind,entities,page in questions:
            result = resolve(question);request = result['request']
            self.assertEqual(result['reason'], 'relation_query_with_provenance')
            self.assertEqual((request['kind'],request['entities'],request['page']), (kind,entities,page))
            self.assertTrue(request['source_evidence_requested'])
            self.assertNotIn('followup', request)

    def test_new_query_does_not_borrow_previous_selected_relation(self):
        text = '黄芪的成分关联哪些靶点，请把对应的原始记录也列出来。'
        request = resolve(text,relation_context={'query_kind':'herb_target_paths','herb_sources':[{'source_record_id':'old'}]})['request']
        self.assertEqual(request['entities'], ['黄芪'])
        self.assertNotIn('action', request)
        request = resolve('这个成分对应靶点的出处在哪？',relation_context={'query_kind':'herb_target_paths'})['request']
        self.assertEqual(request['action'], 'replay')

    def test_provenance_clause_does_not_drop_other_actions_or_record_descriptions(self):
        from tcm.intent_guidance import relation_with_provenance
        for text in (
            '白术的成分关联哪些靶点，请把对应的原始记录也列出来，再找论文。',
            '白术的成分关联哪些靶点，请把对应的原始记录也列出来并说明疗效。',
            '白术和黄芪的成分关联哪些靶点，附上出处。',
            '这个成分有哪些靶点，附上原始记录。',
            '白术这个成分对应哪些靶点，附上原始行。',
            '这些成分对应哪些靶点，附上出处。',
            'CYP24A1有哪些成分 第0页，附上出处。',
        ):
            self.assertIsNone(relation_with_provenance(text), text)
        mixed = '白术的成分关联哪些靶点，请把对应的原始记录也列出来，给我配药'
        self.assertEqual(resolve(mixed)['request']['topic'], 'personal_medication_boundary')

    def test_provenance_queries_keep_explicit_commands_and_natural_switch(self):
        from tcm.intent_guidance import relation_with_provenance
        text = '白术的成分关联哪些靶点，请把对应的原始记录也列出来。'
        with patch.dict(os.environ, {'ENABLE_NATURAL_ROUTING':'0'}):
            self.assertIsNone(resolve(text))
            self.assertEqual(resolve('/relations 成分靶点 BaiZhu')['mode'], 'explicit')
        self.assertIsNone(relation_with_provenance('字'*801))
        self.assertEqual(resolve('/research 白术的功效')['tool'], 'research')

    def test_target_direction_preserves_literal_identifier_and_does_not_drop_half_task(self):
        text='我手里只有一个靶点名字 AKT1，能从它找到相关药材和成分吗？'
        request=resolve(text)['request'];self.assertEqual(request,{'topic':'target_direction_needed','target':'AKT1'})
        result=execute(request);self.assertIn('AKT1关联哪些药材',result['message']);self.assertIn('AKT1有哪些成分',result['message'])
        self.assertNotIn('relations',result)
        for direction,kind in [('药材','target_herb'),('成分','target_ingredient')]:
            r=resolve('我手里只有一个靶点名字 Q07973，能从它找到相关'+direction+'吗？')['request']
            self.assertEqual((r['kind'],r['entities']),(kind,['Q07973']))
        for text in ('靶点 AKT1和TNF，能从它找到相关药材吗？','靶点 芳香化酶，能从它找到相关药材吗？'):
            self.assertIsNone(recognize(text))
        mixed=recognize('靶点 AKT1，能从它找到相关药材吗？再给我开个处方')
        self.assertEqual(mixed['topic'],'personal_medication_boundary')

    def test_disabled_literature_does_not_offer_to_search_after_asking_for_terms(self):
        request=resolve('如果这里没写清楚，你能不能找几篇最近的论文补一下，再把刚才这些内容和出处一起整理成一份我能下载的笔记？')['request']
        result=execute(request);self.assertIn('未启用',result['message']);self.assertIn('下载笔记',result['message'])
        self.assertIn('不会写成已获取证据',result['message'])
        with patch.dict(os.environ,{'ENABLE_PUBMED':'1'}):
            result=execute(request);self.assertNotIn('未启用',result['message']);self.assertIn('英文/拉丁名',result['message'])
        self.assertEqual(resolve('在PubMed检索 Example Herb')['tool'],'pubmed')

    def test_new_guidance_respects_natural_routing_switch_and_command_overrides(self):
        with patch.dict(os.environ,{'ENABLE_NATURAL_ROUTING':'0'}):
            self.assertIsNone(resolve('我手里只有一个靶点名字 AKT1，能从它找到相关药材和成分吗？'))
        self.assertIsNone(resolve('/unknown 我该用什么药'))
        self.assertIsNone(recognize('字'*801+'帮我配药'))

    def test_chat_guidance_does_not_call_tools_models_or_borrow_focus(self):
        import app
        from fastapi.testclient import TestClient
        from agents import agent_decision as ad
        from agents.conversation_store import ConversationStore
        from langchain_core.runnables import RunnableLambda
        def deny(*a,**kw):raise AssertionError('Guidance called a model or data tool')
        with tempfile.TemporaryDirectory() as folder,patch.object(ad,'memory',ConversationStore(Path(folder)/'memory.sqlite3')), \
             patch.object(ad.config.agent_decision,'llm',RunnableLambda(deny)),patch.object(ad.config.conversation,'llm',RunnableLambda(deny)), \
             patch.object(ad.LocalGuardrails,'check_input',deny),patch.object(ad.LocalGuardrails,'check_output',deny), \
             patch.object(ad.research_runtime,'get_service',side_effect=deny), \
             TestClient(app.app) as client,redirect_stdout(io.StringIO()):
            for query,topic in [('看起来有几个同名选项，我该选哪个？','source_selection_help'),
                ('我最近总觉得累，帮我选两味药配着喝','personal_medication_boundary'),
                ('我手里只有一个靶点名字 AKT1，能从它找到相关药材和成分吗？','target_direction_needed'),
                ('找最近论文，再导出笔记','literature_term')]:
                response=client.post('/chat',json={'query':query});self.assertEqual(response.status_code,200,response.text)
                result=response.json()['assistant'];self.assertEqual(result['topic'],topic);self.assertEqual(result['llm_calls'],0)
                self.assertNotIn('relations',result)
