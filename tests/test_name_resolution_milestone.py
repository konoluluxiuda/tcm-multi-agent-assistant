"""Auditable title candidates, immutable snapshots and safe user-facing failures."""
from pathlib import Path
from copy import deepcopy
import json
import os
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage
from tests import test_etcm_enrichment as enrichment_fixture
from tests import test_research_question_api as api_fixture
from tests.test_research_question import draft
from scripts.refresh_etcm_name_candidates import build
from scripts.build_etcm_enrichment import pharma_candidates,digest
from tcm.etcm_name_candidates import title_pinyin
from tcm.dataops.failures import public_failure
from tcm.dataops import diagnostics


class HeadingTests(unittest.TestCase):
    def test_glued_and_separate_headings_retain_exact_source_spans(self):
        for name,text,value in [('丹参','丹参DanshenSALVIAE MILTIORRHIZAE RADIX ET RHIZOMA','Danshen'),
                                ('连翘','连翘Lianqiao FORSYTHIAE FRUCTUS','Lianqiao'),
                                ('丹参',' 丹参 Dan shen SALVIAE RADIX ','Dan shen'),
                                ('测试草','TestHerb TEST HERB','TestHerb')]:
            p=title_pinyin(text,name);self.assertIsNotNone(p,text)
            self.assertEqual(p['value'],value);a,b=p['span'];self.assertEqual(text[a:b],value)
        for name,text in [('丹参','伪丹参Danshen SALVIAE RADIX'),('丹参','丹参DANSHEN SALVIAE RADIX'),
                          ('丹参','丹参Dan.shen SALVIAE RADIX'),('丹参','丹参Danshen'),
                          ('丹参','丹参Danshen SALVIAE RADIX 123')]:
            self.assertIsNone(title_pinyin(text,name),text)

    def test_snapshot_adds_only_proved_names_and_preserves_original_tables(self):
        fixture=enrichment_fixture.EnrichmentTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        # Simulate a historical candidate builder which missed this title.
        with sqlite3.connect(fixture.path) as c:c.execute('DELETE FROM name_candidates')
        original=digest(fixture.path)
        r=build(fixture.path,fixture.entries,fixture.blocks,fixture.root/'new-names')
        self.assertEqual(digest(fixture.path),original);self.assertEqual(r['added_rows'],1)
        addition=r['additions'][0];self.assertFalse(addition['evidence']['identity_verified'])
        self.assertEqual(addition['evidence']['herb_source']['file_sha256'],fixture.record['source']['file_sha256'])
        with sqlite3.connect(fixture.path) as before,sqlite3.connect(fixture.root/'new-names/enrichment.sqlite3') as after:
            for table in ('herbs','edges','nodes','translations','file_candidates'):
                self.assertEqual(before.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall(),
                                 after.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall())
        with self.assertRaises(FileExistsError):build(fixture.path,fixture.entries,fixture.blocks,fixture.root/'new-names')

    def test_ambiguous_pinyin_retains_both_sources_and_preparation_does_not_narrow(self):
        fixture=enrichment_fixture.EnrichmentTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        with sqlite3.connect(fixture.path) as c:
            record=deepcopy(fixture.record);record['source_record_id']='etcm2-local:'+'b'*64
            record['fields']['name_pinyin']['value']='Test-Herb'
            c.execute('INSERT INTO herbs VALUES(?,?,?,?)',(record['source_record_id'],'v1',record['source']['file_sha256'],json.dumps(record)))
        r=build(fixture.path,fixture.entries,fixture.blocks,fixture.root/'ambiguous-names')
        with sqlite3.connect(fixture.root/'ambiguous-names/enrichment.sqlite3') as c:
            self.assertEqual(c.execute('SELECT COUNT(DISTINCT herb_id) FROM name_candidates WHERE name_zh=?',('测试草',)).fetchone()[0],2)
        self.assertFalse(r['identity_verified'])
        self.assertNotEqual(title_pinyin('酒丹参Jiudanshen SALVIAE RADIX','酒丹参')['normalized'],'danshen')


class FailureTests(unittest.TestCase):
    def test_request_verbs_are_not_swallowed_into_a_local_herb_name(self):
        from tcm.research_plan_intent import local_plan
        for q,name in [('想知道连翘在ETCM里的科名以及药用部位是什么。','连翘'),
                       ('告诉我ETCM里丹参的科名和药用部位。','丹参'),
                       ('我想了解ETCM里黄芪的科名和药用部位。','黄芪')]:
            plan=local_plan(q);self.assertIsNotNone(plan,q)
            self.assertEqual(len(plan.steps),1);self.assertEqual(plan.steps[0].call.arguments.name,name)
            self.assertEqual(set(plan.steps[0].call.arguments.fields),{'family','medicinal_part'})
        self.assertIsNone(local_plan('想知道连翘在ETCM里的科名以及药用部位，再预测疗效'))

    def test_literal_desire_and_check_prefixes_ground_without_accepting_negated_mentions(self):
        from tcm.intent_planner import grounded
        for q in ('我想知道丹参与连翘的共同靶点','告诉我丹参的科名','需要核实丹参的药用部位'):
            self.assertTrue(grounded('丹参',q))
        for q in ('不要告诉我丹参的科名','不想知道丹参的靶点','酒丹参的资料','丹参提取物的成分'):
            self.assertFalse(grounded('丹参',q))

    def test_multiple_attribute_fields_preserve_all_slots_and_old_single_field_shape(self):
        from tcm.intent_planner import QueryIntent,mapped
        q='告诉我ETCM里丹参的科名和药用部位。'
        value=QueryIntent(action='herb_field',entities=['丹参'],fields=['family','medicinal_part'])
        result=mapped(value,q)
        self.assertEqual(result['request']['fields'],['family','medicinal_part'])
        self.assertNotIn('field',result['request'])
        with self.assertRaises(ValueError):mapped(QueryIntent(action='herb_field',entities=['丹参'],fields=['family']),q)
        with self.assertRaises(ValueError):QueryIntent(action='herb_field',entities=['丹参'],fields=['family','family'])
        self.assertEqual(mapped(QueryIntent(action='herb_field',entities=['丹参'],fields=['family']),'丹参的科名')['request']['field'],'family')

    def test_card_delivery_cannot_be_downgraded_to_a_simple_query(self):
        from tcm.dataops.research_question import request_proposal
        from tcm.dataops.goal_model import GoalResponseRejected
        class Model:
            max_retries=0;max_tokens=1200;calls=0
            def bind_tools(self,*args,**kwargs):return self
            def invoke(self,messages):
                self.calls+=1
                return AIMessage(content='',tool_calls=[{'id':'query','name':'submit_query_intent',
                                    'args':{'action':'herb_profile','entities':['测试草']}}])
        model=Model()
        with self.assertRaises(GoalResponseRejected) as exc:
            request_proposal(model,'给测试草建一份资料卡',{'测试草':['source']},authorized=True,allow_queries=True)
        self.assertEqual(exc.exception.diagnostics['rejection']['code'],'delivery_routed_to_partial_query')
        self.assertEqual(model.calls,1)

    def test_public_failure_never_exposes_arbitrary_exception_or_location(self):
        f=public_failure('private-key-or-sql','/private/path')
        self.assertEqual(f['code'],'requirements_unresolved');self.assertNotIn('private',json.dumps(f))
        self.assertFalse(f['automatic_retry'])
        self.assertEqual(diagnostics.validation_detail(ValueError('Relation requirements omitted or duplicated'))['code'],
                         'relation_requirement_coverage_failed')

    def test_rejected_model_parameters_have_specific_cause_and_no_retry_on_replay(self):
        helper=api_fixture.QuestionApiTests();helper.setUp();self.addCleanup(helper.doCleanups)
        class Model:
            max_retries=0;max_tokens=1200;model_name='fixture';calls=0
            http_client=SimpleNamespace(close=lambda:None)
            def bind_tools(self,*args,**kwargs):return self
            def invoke(self,messages):
                self.calls+=1
                return AIMessage(content='',tool_calls=[{'id':'bad','name':'propose_research_goal',
                    'args':draft(names=['Alpha'],document_fields=['功能与主治'])}])
        model=Model()
        with patch.dict(os.environ,{'ENABLE_DATAOPS_RESEARCH_GOAL_MODEL':'1','RAG_MODEL':'fixture',
                                    'RAG_BASE_URL':'https://api.deepseek.com','RAG_API_KEY':'test-only'}),\
             patch('tcm.dataops.research_model.configured_model',return_value=model):
            body={'question':'为Alpha和Beta建一份资料卡，摘出主要用途，缺失标明。',
                  'request_key':'failed-model-scope','use_model':True}
            first=helper.client.post('/api/dataops/research-questions',json=body)
            self.assertEqual(first.status_code,200,first.text)
            self.assertNotIn('workspace',first.json());p=first.json()['proposal']
            self.assertEqual(p['failure']['code'],'mentioned_object_omitted')
            self.assertIn('遗漏',p['issues'][0]);self.assertEqual(p['external_model_calls'],1)
            second=helper.client.post('/api/dataops/research-questions',json=body).json()
            self.assertTrue(second['replayed']);self.assertEqual(model.calls,1)
            self.assertEqual(second['proposal']['failure'],p['failure'])

    def test_not_found_and_ambiguous_sources_have_different_reasons(self):
        from tests.test_relation_queries import RelationQueryTests
        from tcm.relation_queries import execute
        fixture=RelationQueryTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        r=execute({'kind':'ingredient','entities':['没有的药'],'page':1,'resolve_unique_sources':True})
        self.assertEqual(r['failure']['code'],'name_not_found');self.assertEqual(r['missing_entities'],['没有的药'])
        with sqlite3.connect(fixture.database) as c:
            c.execute('UPDATE name_candidates SET name_zh=?',('同名草',))
        r=execute({'kind':'ingredient','entities':['同名草'],'page':1,'resolve_unique_sources':True})
        self.assertEqual(r['failure']['code'],'ambiguous_source');self.assertEqual(r['missing_entities'],[])
