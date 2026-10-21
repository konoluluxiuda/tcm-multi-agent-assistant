"""Candidate paths require all three compound fields and keep both source witnesses."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.build_etcm_enrichment import build as build_herbs
from scripts.build_target_ingredient_index import build as build_targets
from tcm.compound_identity import compound_key
from tcm.herbs import import_snapshot, lookup
from tcm.herb_target_paths import query


class HerbTargetPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); source = self.root/'source'; (source/'etcm_herbs').mkdir(parents=True)
        def ingredient(name, formula='C2H6', weight='30.070'):
            return {'Ingredient Name':[name], 'Molecular Formula':formula, 'Molecular Weight':weight}
        self.ingredients = [ingredient('Compound (+)'), ingredient('Compound (-)'), ingredient('Wrong mass', weight='31'), ingredient('Incomplete', weight=None)]
        doc = {'code':1, 'data':[{'id':'base_information','value':[{'key':'Herb Name in Pinyin','value':'TestHerb'}]},
               {'id':'related_table','value':[{'type':'ingredient','value':self.ingredients}]}],
               'complete_fetch_summary':[{'section':'related_table','type':'ingredient','status':'partial'}]}
        (source/'etcm_herbs/TestHerb.json').write_text(json.dumps(doc))
        self.base=self.root/'herbs.sqlite3'; import_snapshot(source,self.base,'v1')
        ep,bp=self.root/'entries.json',self.root/'blocks.json'
        ep.write_text(json.dumps({'entries':[{'id':'entry','name_zh_candidate':'测试药','title_flags':[],'title_block_ids':['title']}]}))
        bp.write_text(json.dumps([{'id':'title','raw':{'text':'TestHerb SYNTHETIC HERB'}}]))
        with redirect_stdout(io.StringIO()):build_herbs(source,self.base,'v1',self.root/'derived',ep,bp)
        self.enrichment=self.root/'derived/enrichment.sqlite3'
        with sqlite3.connect(self.enrichment) as con:
            fingerprint=json.loads(con.execute("SELECT value FROM metadata WHERE key='source_fingerprint'").fetchone()[0])
        audit=self.root/'audit';audit.mkdir();contents=[];rows=[]
        for i,identifier in enumerate(('P12345','Q12345')):
            subject={'Target Name':'Synthetic target '+str(i),'Gene Symbol':'EX'+str(i),'UniProt Accession Number':identifier}
            sha=str(i)*64;path=f'etcm_targets/target_{i}.json'
            contents.append({'status':'source_identity_consistent_not_externally_verified','identity':subject,
                             'file_sha256':sha,'canonical_source':path})
            compound_rows=([ingredient('Compound (+)',weight='30.07')]*25 if i==0 else [ingredient('Compound (+)')])
            if i==0:compound_rows += [ingredient('Wrong mass'),ingredient('Compound (+)',formula='C2H8')]
            for j,raw in enumerate(compound_rows):
                rows.append({'target':subject,'source_file':path,'source_file_sha256':sha,'ingredient_name':raw['Ingredient Name'][0],
                             'raw_row':raw,'json_pointer':f'/data/1/value/0/value/{j}',
                             'capture_status':[{'status':'partial'}],'ingredient_identity_verified':False})
        report={'status':'completed','contents':contents,'dataset_version':'v1','herb_source_fingerprint':fingerprint,
                'exported_target_ingredient_rows':len(rows),'current_herb_uniprot_groups':4}
        (audit/'report.json').write_text(json.dumps(report));(audit/'target_ingredients.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
        self.targets=self.root/'targets.sqlite3';build_targets(audit,self.targets)
        self.record=lookup(self.base,name='TestHerb',dataset_version='v1')['records'][0]
        env=patch.dict(os.environ,{'TCM_DB':str(self.base),'TCM_DATASET_VERSION':'v1','ETCM_ENRICHMENT_DB':str(self.enrichment),
                                  'ETCM_TARGET_DB':str(self.targets),'ENABLE_NATURAL_ROUTING':'1','RESEARCH_TOOLS_ONLY':'1'})
        env.start();self.addCleanup(env.stop)

    def get(self,page=1):return query(self.enrichment,self.targets,self.record,'v1',page=page)

    def test_all_three_fields_match_but_stereo_formula_and_mass_differences_do_not(self):
        result=self.get();coverage=result['coverage']
        self.assertEqual(result['total'],26)
        self.assertEqual(coverage,{'herb_ingredient_source_rows':4,'complete_identity_rows':3,'missing_identity_rows':1,
                         'matching_herb_rows':1,'unmatched_complete_rows':2,'name_only_conflict_rows':1,
                         'distinct_source_uniprot':2,'target_catalog_targets':2,'reference_target_groups':4})
        self.assertTrue(all(r['ingredient_name']=='Compound (+)' for r in result['items']))
        self.assertFalse(result['items'][0]['compound_identity_verified'])

    def test_pages_keep_each_source_row_pair_and_both_capture_statuses(self):
        first,second=self.get(),self.get(2)
        self.assertEqual((len(first['items']),len(second['items'])),(20,6))
        def ids(result):return {(r['herb_evidence']['edge_id'],r['target_evidence']['source_file_sha256'],r['target_evidence']['json_pointer'])for r in result['items']}
        self.assertFalse(ids(first)&ids(second))
        row=first['items'][0]
        self.assertEqual(row['herb_evidence']['json_pointer'],'/data/1/value/0/value/0')
        self.assertEqual(row['herb_evidence']['capture_status'][0]['status'],'partial')
        self.assertEqual(row['target_evidence']['capture_status'][0]['status'],'partial')

    def test_compound_keys_do_not_round_precision_or_ignore_missing_fields(self):
        def row(value):return {'Ingredient Name':['X'],'Molecular Formula':'C2H6','Molecular Weight':value}
        self.assertEqual(compound_key(row('30.070')),compound_key(row('30.07')))
        self.assertNotEqual(compound_key(row('1.00000000000000000000000000001')),compound_key(row('1.00000000000000000000000000002')))
        for value in (None,'NaN','Infinity','0','-1'):
            self.assertIsNone(compound_key(row(value)))

    def test_source_change_legacy_index_and_invalid_page_fail_closed(self):
        bad={**self.record,'source':{**self.record['source'],'file_sha256':'changed'}}
        with self.assertRaises(ValueError):query(self.enrichment,self.targets,bad,'v1')
        with self.assertRaises(ValueError):self.get(0)
        with sqlite3.connect(self.targets) as con:con.execute("DELETE FROM metadata WHERE key='compound_lookup_version'")
        with self.assertRaises(ValueError):self.get()

    def test_empty_result_reports_coverage_without_claiming_no_relationship(self):
        with sqlite3.connect(self.targets) as con:con.execute("UPDATE ingredient_rows SET match_weight='different'")
        result=self.get();self.assertEqual(result['total'],0);self.assertEqual(result['items'],[])
        self.assertEqual(result['coverage']['name_only_conflict_rows'],2)

    def test_chat_chinese_choice_and_graph_do_not_call_models_or_store_raw_paths(self):
        import app
        import agents.agent_decision as ad
        from agents.conversation_store import ConversationStore
        from fastapi.testclient import TestClient
        from langchain_core.runnables import RunnableLambda
        def forbidden(*a,**kw):raise AssertionError('Path query called a model')
        with patch.object(ad,'memory',ConversationStore(self.root/'memory.sqlite3')), \
             patch.object(ad.config.agent_decision,'llm',RunnableLambda(forbidden)), \
             patch.object(ad.config.conversation,'llm',RunnableLambda(forbidden)), \
             patch.object(ad.LocalGuardrails,'check_input',forbidden),patch.object(ad.LocalGuardrails,'check_output',forbidden), \
             TestClient(app.app) as client,redirect_stdout(io.StringIO()):
            first=client.post('/chat',json={'query':'测试药的成分关联哪些靶点'}).json()['assistant']
            self.assertEqual(first['status'],'clarification_needed');self.assertNotIn('data',first['relations'])
            identifier=first['relations']['groups'][0]['options'][0]['source_record_id']
            response=client.post('/chat',json={'query':'/relations 成分靶点 '+identifier})
            self.assertEqual(response.status_code,200)
            result=response.json()['assistant'];self.assertEqual(result['relations']['data']['total'],26)
            self.assertIn('候选路径',result['message']);self.assertEqual(result['llm_calls'],0)
            self.assertEqual(client.get('/conversation').json()['assistant']['relations']['data']['total'],26)
            snapshot=ad.memory.load_serialized(client.cookies[app.SESSION_COOKIE_NAME])
            self.assertNotIn('Compound (+)',json.dumps(snapshot.tcm_context)+json.dumps(snapshot.messages))
            with patch.dict(os.environ,{'ETCM_TARGET_DB':''}):
                self.assertEqual(client.post('/chat',json={'query':'TestHerb的成分关联哪些靶点'}).status_code,503)

    def test_named_paths_with_provenance_require_selection_and_keep_both_original_rows(self):
        import app
        import agents.agent_decision as ad
        from agents.conversation_store import ConversationStore
        from fastapi.testclient import TestClient
        from langchain_core.runnables import RunnableLambda
        def forbidden(*args,**kwargs):raise AssertionError('Query must not call models')
        with patch.object(ad,'memory',ConversationStore(self.root/'provenance-memory.sqlite3')), \
             patch.object(ad.config.agent_decision,'llm',RunnableLambda(forbidden)), \
             patch.object(ad.config.conversation,'llm',RunnableLambda(forbidden)), \
             patch.object(ad.LocalGuardrails,'check_input',forbidden),patch.object(ad.LocalGuardrails,'check_output',forbidden), \
             TestClient(app.app) as client,redirect_stdout(io.StringIO()):
            response=client.post('/chat',json={'query':'测试药的成分关联哪些靶点，请把对应的原始记录也列出来。','answer_mode':'auto'})
            self.assertEqual(response.status_code,200)
            body=response.json();self.assertEqual(body['routing']['reason'],'relation_query_with_provenance')
            first=body['assistant'];self.assertEqual(first['status'],'clarification_needed')
            self.assertNotIn('data',first['relations'])
            identifier=first['relations']['groups'][0]['options'][0]['source_record_id']
            chosen=client.post('/chat',json={'query':'/relations 成分靶点 '+identifier,'answer_mode':'auto'}).json()['assistant']
            self.assertEqual(chosen['relations']['data']['total'],26)
            for row in chosen['relations']['data']['items']:
                h,t=row['herb_evidence'],row['target_evidence']
                self.assertIn('raw_row',h);self.assertIn('raw_row',t)
                self.assertEqual(h['json_pointer'],'/data/1/value/0/value/0')
                self.assertTrue(t['json_pointer'].startswith('/data/1/value/0/value/'))
                self.assertFalse(row['compound_identity_verified'])
            self.assertEqual(chosen['llm_calls'],0)


if __name__=='__main__':unittest.main()
