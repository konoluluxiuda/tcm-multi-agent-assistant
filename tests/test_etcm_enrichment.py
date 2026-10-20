"""Derived bilingual data must not overwrite source values or invent verified entity links."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.build_etcm_enrichment import build
from tcm.etcm_enrichment import connect,enrich_records,relations
from tcm.etcm_localization import dictionary_translation
from tcm.herbs import import_snapshot,lookup


class EnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source';(self.source/'etcm_herbs').mkdir(parents=True)
        for name,label in [('TestHerb','Target Alpha'),('SecondHerb','Target Beta')]:
            obj={'code':1,'data':[{'id':'base_information','value':[
                {'key':'Herb Name in Pinyin','value':name},{'key':'Property','value':'Warm'},
                {'key':'Flavor','value':'Bitter, Sweet'},{'key':'Indications','value':'Synthetic unknown wording'},
                {'key':'PubMed','value':['12345']}]},
                {'id':'related_table','value':[
                    {'type':'ingredient','value':[{'Ingredient Name':['Example ingredient']}]},
                    {'type':'target','value':[{'Target Name':[label],'UniProt Accession Number':['P12345']}]},
                    {'type':'disease','value':[{'Disease Name':['Synthetic disease']}]}]}],
                'complete_fetch_summary':[{'section':'related_table','type':'disease','status':'skipped','actual':1}]}
            (self.source/f'etcm_herbs/{name}.json').write_text(json.dumps(obj))
        (self.source/'etcm_targets').mkdir()
        (self.source/'etcm_targets/Target_Alpha.json').write_text('{"name":"WRONG IDENTITY"}')
        self.base=self.root/'herbs.sqlite3';import_snapshot(self.source,self.base,'v1')
        self.entries=self.root/'entries.json';self.blocks=self.root/'blocks.json'
        self.entries.write_text(json.dumps({'entries':[{'id':'entry-1','name_zh_candidate':'测试草','title_flags':[],
                                                       'title_block_ids':['title-1']}]}))
        self.blocks.write_text(json.dumps([{'id':'title-1','raw':{'text':'TestHerb TEST HERB'},'source':{'source_pdf_page':1}}]))
        self.output=self.root/'derived';build(self.source,self.base,'v1',self.output,self.entries,self.blocks)
        self.path=self.output/'enrichment.sqlite3'
        self.record=lookup(self.base,name='TestHerb',dataset_version='v1')['records'][0]

    def test_dictionary_preserves_ambiguous_or_unknown_values(self):
        self.assertEqual(dictionary_translation('properties','Warm')['text'],'温')
        self.assertEqual(dictionary_translation('flavors','Bitter, Sweet')['text'],'苦、甘')
        self.assertIsNone(dictionary_translation('medicinal_part','Unknown organ'))
        self.assertEqual(dictionary_translation('medicinal_part','Root')['text'],'根')

    def test_full_build_has_source_pointers_and_does_not_modify_inputs(self):
        with closing(connect(self.path)) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM herbs').fetchone()[0],2)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM edges').fetchone()[0],8)
            # Same explicit UniProt groups two source assertions, without merging source herbs.
            self.assertEqual(con.execute("SELECT COUNT(*) FROM nodes WHERE kind='target'").fetchone()[0],1)
        result=relations(self.path,self.record,'v1',kind='target')
        item=result['items'][0]
        self.assertEqual(item['source_pointer'],'/data/1/value/1/value/0')
        self.assertIn('not_externally_verified',item['identity_status'])
        self.assertEqual(item['file_candidates'][0]['status'],'filename_match_only_internal_identity_unverified')
        self.assertEqual(item['raw_row']['UniProt Accession Number'],['P12345'])
        self.assertEqual(item['other_herb_sources'][0]['name_pinyin'],'SecondHerb')

    def test_skipped_capture_and_source_labels_remain_visible(self):
        data=relations(self.path,self.record,'v1',kind='disease')
        self.assertEqual(data['items'][0]['capture_status'][0]['status'],'skipped')
        second=lookup(self.base,name='SecondHerb',dataset_version='v1')['records'][0]
        item=relations(self.path,second,'v1',kind='target')['items'][0]
        self.assertEqual(item['name'],'Target Beta');self.assertIn('identity_note',item)

    def test_translation_overlay_preserves_raw_value_and_does_not_select_chinese_identity(self):
        with patch.dict(os.environ,{'ETCM_ENRICHMENT_DB':str(self.path)}):
            record=enrich_records([self.record],'v1')[0]
        self.assertEqual(record['fields']['properties']['value'],'Warm')
        self.assertEqual(record['fields']['properties']['translation']['text'],'温')
        self.assertFalse(record['name_zh_candidates'][0]['identity_verified'])
        self.assertIsNone(record['name_zh'])
        self.assertEqual(record['fields']['indications']['translation']['status'],'pending_local_translation')

    def test_changed_source_or_version_does_not_use_stale_overlay(self):
        bad={**self.record,'source':{**self.record['source'],'file_sha256':'changed'}}
        with self.assertRaises(ValueError):relations(self.path,bad,'v1')
        with patch.dict(os.environ,{'ETCM_ENRICHMENT_DB':str(self.path)}):
            result=enrich_records([bad],'v1')[0]
            self.assertEqual(result['enrichment_status'],'source_mismatch')
            self.assertNotIn('translation',result['fields']['properties'])

    def test_chat_requires_source_choice_for_chinese_candidates(self):
        from tcm.chat import execute_turn
        with patch.dict(os.environ,{'ETCM_ENRICHMENT_DB':str(self.path),'TCM_DB':str(self.base),'TCM_DATASET_VERSION':'v1'}):
            result,context=execute_turn({'name':'测试草'},None)
            self.assertEqual(result['status'],'name_mapping_candidates');self.assertIsNone(context)
            chosen,context=execute_turn({'source_record_id':result['records'][0]['source_record_id']},None)
            self.assertEqual(chosen['status'],'ok');self.assertEqual(context['status'],'selected')

    def test_missing_sidecar_falls_back_to_original_data(self):
        with patch.dict(os.environ,{'ETCM_ENRICHMENT_DB':str(self.root/'absent.sqlite3')}):
            record=enrich_records([self.record],'v1')[0]
        self.assertEqual(record['enrichment_status'],'unavailable')
        self.assertEqual(record['fields']['properties']['value'],'Warm')
        self.assertFalse((self.root/'absent.sqlite3').exists())

    def test_changed_raw_file_and_reused_output_are_rejected(self):
        with self.assertRaises(FileExistsError):build(self.source,self.base,'v1',self.output)
        path=self.source/'etcm_herbs/TestHerb.json';path.write_text(path.read_text()+'\n')
        with self.assertRaises(ValueError):build(self.source,self.base,'v1',self.root/'changed')
        with self.assertRaises(ValueError):connect(self.root/'changed/enrichment.sqlite3')

    def test_pagination_and_invalid_queries_are_bounded(self):
        first=relations(self.path,self.record,'v1',limit=2)
        second=relations(self.path,self.record,'v1',limit=2,offset=first['next_offset'])
        self.assertFalse({e['edge_id'] for e in first['items']} & {e['edge_id'] for e in second['items']})
        for kwargs in ({'limit':101},{'limit':0},{'offset':-1},{'kind':'arbitrary-sql'}):
            with self.assertRaises(ValueError):relations(self.path,self.record,'v1',**kwargs)

    def test_http_relations_are_readonly_and_validate_parameters(self):
        import app
        from fastapi.testclient import TestClient
        before=hashlib.sha256(self.base.read_bytes()).hexdigest()
        with patch.dict(os.environ,{'ETCM_ENRICHMENT_DB':str(self.path),'TCM_DB':str(self.base),'TCM_DATASET_VERSION':'v1'}),TestClient(app.app) as client:
            params={'source_record_id':self.record['source_record_id'],'kind':'ingredient'}
            result=client.get('/api/etcm/relations',params=params)
            self.assertEqual(result.status_code,200);self.assertEqual(result.json()['total'],1)
            self.assertEqual(client.get('/api/etcm/relations',params={**params,'limit':200}).status_code,400)
            self.assertEqual(client.get('/api/etcm/relations',params={**params,'kind':'bad'}).status_code,400)
        self.assertEqual(hashlib.sha256(self.base.read_bytes()).hexdigest(),before)


if __name__=='__main__':unittest.main()
