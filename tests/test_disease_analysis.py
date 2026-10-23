"""Chat disease ranking must preserve source scope, denominators and replay versions."""
from contextlib import redirect_stdout
import copy
import io
import json
import os
import sqlite3
import unittest
from unittest.mock import patch

from scripts.build_etcm_diseases import build
from tests import test_intent_planner as fixtures
from tests.test_disease_catalog import document
from tcm.analysis_tools import invoke,execute_turn,tool_specs
from tcm.analysis_chat import parse
from tcm.intent_planner import plan_question


class DiseaseAnalysisTests(unittest.TestCase):
    def setUp(self):
        fixtures.IntentGraphTests.setUp(self)
        source=self.root/'disease-source';directory=source/'etcm_diseases';directory.mkdir(parents=True)
        self.disease_source=source
        for name,ids in [('Synthetic disease',['P00000','Q00000','R99999']),('Same set',['P00000','Q00000','R99999']),('Empty',[]),('Disjoint',['R88888'])]:
            d=document(name);d['data'][1]['value'][0]['value']=[{'UniProt Accession Number':[i],'Gene Symbol':['X'+str(n)]} for n,i in enumerate(ids)]
            if name=='Synthetic disease':d['data'][1]['value'][0]['value']*=8
            d['complete_fetch_summary'][0].update(actual=len(d['data'][1]['value'][0]['value']),expected=len(d['data'][1]['value'][0]['value']))
            (directory/(name.replace(' ','_')+'.json')).write_text(json.dumps(d))
        with redirect_stdout(io.StringIO()):build(source,self.database,self.root/'diseases','disease-v1')
        self.disease_db=self.root/'diseases/diseases.sqlite3'
        p=patch.dict(os.environ,{'ETCM_DISEASE_DB':str(self.disease_db)});p.start();self.addCleanup(p.stop)

    def turn(self,q,**kw):
        with redirect_stdout(io.StringIO()):return self.client.post('/chat',json={'query':q,**kw})

    def call(self,tool='rank_herbs_for_gene_set',name='Synthetic disease',**kwargs):
        return invoke({'tool':tool,'arguments':{'name':name,**kwargs}})

    def test_math_full_denominators_and_both_original_witnesses(self):
        result=self.call();self.assertEqual(result.status,'ok');d=result.data
        self.assertEqual(d.background_target_ids,['P00000','Q00000']);self.assertEqual(d.outside_background_ids,['R99999'])
        self.assertEqual(d.identical_set_sources,2);self.assertEqual(len(d.items),2)
        first=d.items[0];self.assertEqual(first.shared_count,2);self.assertEqual(first.union_count,25);self.assertEqual(first.jaccard,2/25)
        self.assertEqual(first.query_only_ids,['R99999']);self.assertEqual(d.profile['species_status'],'not_recorded')
        from tcm.disease_catalog import verify_witness
        for pair in first.evidence:
            for row in pair['disease_rows']:self.assertTrue(verify_witness(self.disease_source,d.profile,row))
            herb=json.loads((self.root/'source'/first.source.source['relative_file_path']).read_text())
            for row in pair['herb_rows']:
                raw=herb
                for token in row['json_pointer'].split('/')[1:]:raw=raw[int(token)] if isinstance(raw,list) else raw[token]
                self.assertEqual(raw,row['raw_row'])
        same=self.call(name='Same set').data
        self.assertEqual([(r.source_record_id,r.jaccard) for r in d.items],[(r.source_record_id,r.jaccard) for r in same.items])

    def test_profile_pagination_and_zero_or_disjoint_sets(self):
        first=self.call('disease_gene_profile').data;second=self.call('disease_gene_profile',page=2).data
        self.assertEqual((len(first.rows),len(second.rows)),(20,4));self.assertTrue(first.has_next)
        self.assertFalse(second.has_next)
        for name in ('Empty','Disjoint'):
            result=self.call(name=name);self.assertEqual(result.status,'empty');self.assertFalse(result.data.items)
        self.assertEqual(self.call(name='Disjoint').data.outside_background_ids,['R88888'])

    def test_size_context_uses_background_and_rejects_corrupt_explanations(self):
        data=self.call().data
        self.assertEqual(data.size_context.query_background_count,2)
        self.assertEqual(data.size_context.background_size,data.coverage.distinct_identifiers)
        for item in data.items:
            expected=2*item.target_count/data.coverage.distinct_identifiers
            self.assertAlmostEqual(item.expected_shared_count,expected)
            self.assertAlmostEqual(item.observed_minus_expected,item.shared_count-expected)
        for field in ('expected_shared_count','observed_minus_expected'):
            bad=data.model_dump();bad['items'][0][field]+=1
            with patch('tcm.disease_analysis.query',return_value=bad):
                self.assertEqual(self.call().error.code,'invalid_result')
        bad=data.model_dump();bad['size_context']['background_size']+=1
        with patch('tcm.disease_analysis.query',return_value=bad):
            self.assertEqual(self.call().error.code,'invalid_result')
        self.assertEqual(self.call(name='Disjoint').data.size_context.query_background_count,0)

    def test_natural_chat_replay_and_evidence_mode_never_call_model(self):
        with patch('tcm.intent_planner.plan_question',side_effect=AssertionError('Unnecessary model call')):
            r=self.turn('查询疾病 Synthetic disease 的靶点资料',answer_mode='evidence').json()
            self.assertEqual(r['assistant']['analysis_result']['tool'],'disease_gene_profile')
            r=self.turn('按疾病 Synthetic disease 的靶点找候选药材，前5个').json()
            self.assertEqual(r['assistant']['analysis_result']['data']['top_k'],5)
            r=self.turn('回看分析结果').json();self.assertEqual(r['assistant']['analysis_result']['tool'],'rank_herbs_for_gene_set')
            restored=self.client.get('/conversation').json()['assistant']['analysis_result']['data']
            self.assertEqual(restored['top_k'],5)
        cookie=self.client.cookies[self.app.SESSION_COOKIE_NAME];saved=self.memory.load(cookie)
        self.assertNotIn('R99999',str(saved));self.assertNotIn('herb_rows',str(saved))
        with patch.object(self.ad,'memory',type(self.memory)(self.memory.path)):
            self.assertEqual(self.client.get('/conversation').json()['assistant']['analysis_result']['tool'],'rank_herbs_for_gene_set')

    def test_unknown_and_ambiguous_disease_dont_guess_or_rename(self):
        r=self.turn('查询疾病 糖尿病 的靶点资料').json()['assistant'];self.assertEqual(r['status'],'clarification_needed')
        with sqlite3.connect(self.disease_db) as con:
            con.execute("UPDATE diseases SET name_key='synthetic disease' WHERE name='Same set'")
        r=self.turn('疾病 Synthetic disease 的候选药材').json()['assistant'];self.assertEqual(len(r['options']),2)
        self.assertEqual(self.turn(r['options'][0]['query']).json()['assistant']['status'],'ok')

    def test_background_change_and_catalog_change_stop_old_analysis(self):
        r,focus=execute_turn(parse('疾病 Synthetic disease 的候选药材'))
        with sqlite3.connect(self.disease_db) as con:con.execute("UPDATE metadata SET value='\"changed\"' WHERE key='source_fingerprint'")
        r,_=execute_turn({'followup':True},focus);self.assertEqual(r['status'],'unavailable')
        with sqlite3.connect(self.database) as con:con.execute("UPDATE metadata SET value='\"changed\"' WHERE key='source_fingerprint'")
        self.assertEqual(self.call().error.code,'snapshot_changed')
        self.assertEqual(self.call('disease_gene_profile').status,'ok')

    def test_rule_and_model_scope_preserves_identity_and_bounds(self):
        for q in ('疾病 Synthetic disease 的候选药材，还要处方','疾病 Synthetic disease 的治疗候选药材'):
            self.assertIsNone(parse(q))
        plan={'action':'analysis','analysis':{'tool':'rank_herbs_for_gene_set','arguments':{'name':'Synthetic disease','top_k':5}},'entities':['Synthetic disease']}
        r=plan_question('按 Synthetic disease 的来源靶点找前5个草药候选',sdk=fixtures.Model(plan));self.assertEqual(r['intent_status'],'ok')
        for q in ('按糖尿病的来源靶点找前5个草药候选','Synthetic disease 治疗用药前5个','Synthetic disease 的候选药材前30个'):
            self.assertEqual(plan_question(q,sdk=fixtures.Model(plan))['intent_status'],'invalid_output')
        self.assertEqual(self.call(top_k=21).error.code,'invalid_arguments')

    def test_mocked_model_selects_local_disease_tool_with_current_question_only(self):
        model=fixtures.Model({'action':'analysis','analysis':{'tool':'disease_gene_profile','arguments':{'name':'Synthetic disease'}}})
        self.turn('介绍两个数据库')
        with patch('config.create_chat_model',return_value=model):
            r=self.turn('能为 Synthetic disease 整理一张靶点资料卡吗').json()
        self.assertEqual(r['model_calls'],1);self.assertEqual(r['assistant']['analysis_result']['status'],'ok')
        self.assertEqual(len(model.inputs[0]),2);self.assertNotIn('R99999',str(model.inputs));self.assertNotIn('介绍两个数据库',str(model.inputs))

    def test_disabled_catalog_and_malformed_output_fail_without_fallback(self):
        with patch.dict(os.environ,{'ETCM_DISEASE_DB':''}):
            r,_=execute_turn(parse('疾病 Synthetic disease 的候选药材'));self.assertEqual(r['status'],'unavailable')
            self.assertFalse(next(s for s in tool_specs() if s['name']=='disease_gene_profile')['configured'])
        valid=self.call().data.model_dump();valid['outside_background_ids']=[]
        with patch('tcm.disease_analysis.query',return_value=valid):self.assertEqual(self.call().error.code,'invalid_result')
        valid=self.call().data.model_dump();valid['items'][0]['evidence'][0]['disease_rows']=[]
        with patch('tcm.disease_analysis.query',return_value=valid):self.assertEqual(self.call().error.code,'invalid_result')


if __name__=='__main__':unittest.main()
