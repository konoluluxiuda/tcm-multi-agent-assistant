"""Natural intents, bounded inventory and actual graph/API without models or network."""
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tcm.assistant_router import resolve
from tcm.assistant_info import herb_inventory
from tcm.herbs import import_snapshot
from tests.test_mineru_chat import Service
from tests.test_mineru_retrieval import fixture
from scripts.build_mineru_retrieval import build


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.p = patch.dict(os.environ, {'ENABLE_NATURAL_ROUTING':'1'})
        self.p.start(); self.addCleanup(self.p.stop)

    def test_overview_and_capabilities_do_not_require_commands(self):
        for text in ('支持多少药材','药材有多少种','收录了多少药材','有多少种功效','有哪些功效分类','数据概览'):
            self.assertEqual(resolve(text)['request']['topic'],'overview',text)
        for text in ('你能做什么','系统有什么功能','支持哪些功能','可以查什么'):
            self.assertEqual(resolve(text)['request']['topic'],'capabilities',text)

    def test_individual_efficacy_is_not_a_global_inventory_question(self):
        for text in ('黄芪的功效有哪些','黄芪有什么作用','黄芪的用量是多少','查一下黄芪的药用部位'):
            self.assertEqual(resolve(text)['tool'],'research',text)
        self.assertEqual(resolve('什么是归经')['request']['topic'],'concept')
        self.assertEqual(resolve('黄芪的归经是什么')['tool'],'research')

    def test_legacy_parsers_and_explicit_overrides_are_preserved(self):
        self.assertEqual(resolve('/herb 黄芪的功效')['tool'],'herb')
        self.assertEqual(resolve('/research')['request'],{'error':'invalid_query'})
        self.assertEqual(resolve('/pubmed')['request'],{'action':'invalid'})
        self.assertEqual(resolve('/pubmed ' + 'x'*2001)['request'],{'action':'invalid'})
        with patch.dict(os.environ, {'ENABLE_NATURAL_ROUTING':'0'}):
            self.assertIsNone(resolve('黄芪的功效'))
            self.assertEqual(resolve('它的药用部位是什么')['tool'],'herb')

    def test_followup_uses_current_source_and_never_maps_across_sources(self):
        self.assertEqual(resolve('它的药用部位',research_context={'name':'黄芪'})['tool'],'research')
        self.assertEqual(resolve('它的药用部位',herb_context={'status':'selected'})['tool'],'herb')
        self.assertEqual(resolve('用量呢',herb_context={'status':'selected'})['request']['topic'],'research_name_needed')
        self.assertEqual(resolve('出处呢',pubmed_context={'kind':'pubmed_run_v1'})['tool'],'pubmed')
        self.assertEqual(resolve('用量呢')['request']['question'],'它的用量')

    def test_literature_query_preserves_term_and_never_guesses_chinese_mapping(self):
        for text in ('在PubMed检索 Astragalus membranaceus[Title/Abstract]', '帮我找 Astragalus membranaceus 的相关文献'):
            r=resolve(text);self.assertEqual(r['tool'],'pubmed',text)
            self.assertIn('Astragalus membranaceus',r['request']['query'])
        self.assertEqual(resolve('找黄芪相关文献')['request']['topic'],'literature_term')
        self.assertEqual(resolve('黄芪的药典记载和相关文献')['request']['topic'],'clarify_sources')

    def test_names_are_checked_against_the_verified_index_and_images_excluded(self):
        with patch('tcm.assistant_router.runtime.recognizes_name',return_value=True):
            self.assertEqual(resolve('黄芪')['request'],{'question':'黄芪'})
        with patch('tcm.assistant_router.runtime.recognizes_name',return_value=False):
            self.assertIsNone(resolve('你好'))
        self.assertIsNone(resolve({'image':'private','text':'黄芪的功效'}))
        self.assertIsNone(resolve('/unknown 黄芪的功效'))


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source';(self.source/'etcm_herbs').mkdir(parents=True)
        self.db=self.root/'herbs.sqlite3'

    def source_record(self,name,value=None):
        fields=[{'key':'Herb Name in Pinyin','value':name}]
        if value is not None:fields.append({'key':'Herb classification based on efficacy','value':value})
        p=self.source/'etcm_herbs'/(name+'.json')
        p.write_text(json.dumps({'code':1,'data':[{'id':'base_information','value':fields}]}))

    def test_counts_are_versioned_raw_values_with_missing_and_invalid_excluded(self):
        self.source_record('Alpha','A, B');self.source_record('Beta','A, B');self.source_record('Gamma')
        import_snapshot(self.source,self.db,'v1')
        self.source_record('Gamma','A');import_snapshot(self.source,self.db,'v2')
        v1=herb_inventory(self.db,'v1');v2=herb_inventory(self.db,'v2')
        self.assertEqual((v1['source_record_count'],v1['raw_efficacy_value_count'],v1['efficacy_missing_records']),(3,1,1))
        self.assertEqual((v2['raw_efficacy_value_count'],v2['efficacy_missing_records']),(2,0))
        self.assertEqual(herb_inventory(self.db,'absent')['status'],'dataset_not_found')
        with sqlite3.connect(self.db) as d:d.execute("UPDATE records SET state='quarantined' WHERE dataset_version='v2' AND relative_path LIKE '%Beta.json'")
        self.assertEqual(herb_inventory(self.db,'v2')['source_record_count'],2)

    def test_inventory_is_readonly_and_does_not_create_missing_database(self):
        self.source_record('Alpha','A');import_snapshot(self.source,self.db,'v1')
        before=self.db.read_bytes();herb_inventory(self.db);self.assertEqual(before,self.db.read_bytes())
        absent=self.root/'absent.sqlite3'
        with self.assertRaises(sqlite3.Error):herb_inventory(absent)
        self.assertFalse(absent.exists())


class GraphTests(unittest.TestCase):
    def setUp(self):
        import agents.agent_decision as ad
        import app
        from agents.conversation_store import ConversationStore
        from langchain_core.runnables import RunnableLambda
        from fastapi.testclient import TestClient
        self.ad=ad;self.app=app
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        fixture(self.root/'view');build(self.root/'view',self.root/'index');self.service=Service(self.root/'index')
        self.service.names={'示例叶炭'}
        self.memory=ConversationStore(self.root/'memory.sqlite3')
        def forbidden(*args,**kwargs):raise AssertionError('natural tool route reached a model')
        patches=[patch.dict(os.environ,{'ENABLE_NATURAL_ROUTING':'1','RESEARCH_TOOLS_ONLY':'1','TCM_DB':str(self.root/'absent.sqlite3')}),
                 patch.object(ad,'memory',self.memory),patch.object(ad.research_runtime,'get_service',return_value=self.service),
                 patch.object(ad.config.agent_decision,'llm',RunnableLambda(forbidden)),
                 patch.object(ad.config.conversation,'llm',RunnableLambda(forbidden)),
                 patch.object(ad.LocalGuardrails,'check_input',forbidden),patch.object(ad.LocalGuardrails,'check_output',forbidden)]
        for p in patches:p.start()
        self.addCleanup(lambda:[p.stop() for p in reversed(patches)])
        self.client=TestClient(app.app);self.addCleanup(self.client.close)

    def turn(self,text,client=None):
        with contextlib.redirect_stdout(io.StringIO()):return (client or self.client).post('/chat',json={'query':text})

    def test_natural_fields_followups_restart_and_source_card(self):
        first=self.turn('示例叶炭有什么作用').json()
        self.assertEqual(first['research']['status'],'candidates');self.assertEqual(first['routing']['mode'],'natural')
        with patch.object(self.ad,'memory',type(self.memory)(self.memory.path)):
            follow=self.turn('用量呢').json()
        self.assertEqual(follow['research']['resolved_question'],'示例叶炭的用量')
        self.assertEqual(self.turn('出处呢').json()['research']['status'],'candidates')

    def test_followups_are_isolated_and_summary_does_not_clear_active_focus(self):
        from fastapi.testclient import TestClient
        self.turn('示例叶炭的功效')
        self.assertEqual(self.turn('系统有什么功能').json()['assistant']['topic'],'capabilities')
        self.assertEqual(self.turn('用量呢').json()['research']['status'],'candidates')
        with TestClient(self.app.app) as other:
            self.assertEqual(self.turn('用量呢',other).json()['research']['status'],'clarify_entity')
        self.assertEqual(self.turn('hello').status_code,403)

    def test_inventory_partial_source_failure_and_concept_are_explicit(self):
        data=self.turn('支持多少药材').json()
        self.assertEqual(data['assistant']['status'],'partial')
        self.assertEqual(data['assistant']['pharmacopoeia']['entry_count'],2)
        self.assertNotIn(str(self.root),str(data))
        self.assertEqual(self.turn('什么是归经').json()['assistant']['status'],'clarification_needed')
        self.assertEqual(self.turn('找黄芪相关文献').json()['assistant']['status'],'clarification_needed')

    def test_raw_category_values_stay_out_of_memory_and_inventory_is_live(self):
        source=self.root/'source';(source/'etcm_herbs').mkdir(parents=True)
        obj={'code':1,'data':[{'id':'base_information','value':[
            {'key':'Herb Name in Pinyin','value':'Alpha'},
            {'key':'Herb classification based on efficacy','value':'<img src=x onerror=alert(1)> / source-label'}]}]}
        (source/'etcm_herbs/Alpha.json').write_text(json.dumps(obj))
        db=self.root/'herbs.sqlite3';import_snapshot(source,db,'v1')
        with patch.dict(os.environ,{'TCM_DB':str(db),'TCM_DATASET_VERSION':'v1'}):
            result=self.turn('有哪些功效分类').json()
            self.assertEqual(result['assistant']['herbs']['raw_efficacy_values'][0]['value'],'<img src=x onerror=alert(1)> / source-label')
            cookie=self.client.cookies[self.app.SESSION_COOKIE_NAME]
            self.assertNotIn('source-label',str(self.memory.load(cookie)))
            self.assertEqual(result['assistant']['herbs']['source_record_count'],1)
            with sqlite3.connect(db) as d:d.execute("UPDATE records SET state='quarantined'")
            self.assertEqual(self.turn('有多少药材').json()['assistant']['herbs']['source_record_count'],0)

    def test_disabled_research_keeps_503_and_no_fallback(self):
        with patch.object(self.ad.research_runtime,'get_service',return_value=None):
            self.assertEqual(self.turn('示例叶炭的功效').status_code,503)
        with patch.object(self.ad.research_runtime,'get_service',side_effect=FileNotFoundError('/private/missing')):
            result=self.turn('有多少药材')
            self.assertEqual(result.status_code,503);self.assertNotIn('/private/',result.text)

    def test_bare_name_is_selected_from_index_and_image_path_not_accepted(self):
        self.assertEqual(self.turn('示例叶炭').json()['research']['status'],'field_not_resolved')
        self.assertEqual(self.turn('用量呢').json()['research']['status'],'candidates')
