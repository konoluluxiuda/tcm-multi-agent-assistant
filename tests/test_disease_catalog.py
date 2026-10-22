"""Disease indices must preserve missingness, source scope and immutable witnesses."""
from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.build_etcm_diseases import build
from tcm.disease_catalog import inspect_document, lookup, connect, verify_witness


def document(name='Synthetic disease'):
    return {'code': 1, 'name': name, 'page_title': f'Disease {name} Details page',
            'data': [{'id': 'base_information', 'value': [{'key': 'Disease Name', 'value': name}]},
                     {'id': 'related_table', 'value': [{'type': 'target', 'value': [
                         {'UniProt Accession Number': ['P12345'], 'Gene Symbol': ['GENE1']},
                         {'UniProt Accession Number': ['P12345'], 'Gene Symbol': ['GENE1B']},
                         {'UniProt Accession Number': ['Q12345'], 'Gene Symbol': ['GENE2']},
                         {'UniProt Accession Number': ['P12345','Q12345'], 'Gene Symbol': []}]}]}],
            'complete_fetch_summary': [{'section': 'related_table', 'type': 'target', 'status': 'ok', 'actual': 4, 'expected': 4}]}


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name);self.source = self.root/'source';self.directory = self.source/'etcm_diseases';self.directory.mkdir(parents=True)
        self.file = self.directory/'Synthetic_disease.json';self.file.write_text(json.dumps(document()))
        self.herbs = self.root/'herbs.sqlite3'
        with sqlite3.connect(self.herbs) as db:
            db.executescript('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE nodes(node_id TEXT,identifier TEXT,kind TEXT,identity_status TEXT);CREATE TABLE edges(node_id TEXT,kind TEXT);')
            db.executemany('INSERT INTO metadata VALUES (?,?)', [(k,json.dumps(v)) for k,v in {'kind':'etcm_enrichment_v1','build_complete':True,'dataset_version':'herb-v1','source_fingerprint':'herb-sha'}.items()])
            db.execute("INSERT INTO nodes VALUES ('n','P12345','target','source_declared_uniprot_not_externally_verified')")
            db.execute("INSERT INTO edges VALUES ('n','target')")

    def build(self, name='out'):
        out = self.root/name
        with redirect_stdout(io.StringIO()):result = build(self.source,self.herbs,out,'disease-v1')
        return out/'diseases.sqlite3',result

    def test_counts_missingness_provenance_and_background(self):
        before = self.file.read_bytes();herbs_before = self.herbs.read_bytes()
        db, report = self.build();result = lookup(db,'Synthetic disease');p = result['profile']
        self.assertEqual((p['target_count'],p['target_row_count'],p['duplicate_identifier_rows'],p['unidentified_rows']),(2,4,1,1))
        self.assertEqual(p['conflicting_gene_identifiers'],['P12345'])
        self.assertEqual(p['species_status'],'not_recorded');self.assertEqual(p['capture_state'],'source_reported_complete')
        self.assertEqual(p['background_overlap']['target_count'],1)
        self.assertEqual(result['target_ids'],['P12345','Q12345']);self.assertFalse(result['has_next'])
        for row in result['rows']:self.assertTrue(verify_witness(self.source,p,row))
        self.assertEqual(self.file.read_bytes(),before);self.assertEqual(self.herbs.read_bytes(),herbs_before)
        self.assertEqual(report['sources_with_conflicting_gene_symbols'],1)
        self.assertFalse(report['clinical_or_species_validation'])
        self.assertEqual(lookup(db,result['record_id'])['profile'],p)

    def test_identity_mismatch_and_page_conflict_are_quarantined(self):
        (self.directory/'Wrong.json').write_bytes(self.file.read_bytes())
        bad = document('Other disease');bad['name']='Wrong page'
        (self.directory/'Other_disease.json').write_text(json.dumps(bad))
        db,r = self.build();self.assertEqual(r['indexed_sources'],1)
        self.assertEqual(r['file_status_counts']['filename_internal_identity_mismatch'],1)
        self.assertEqual(r['file_status_counts']['page_name_conflict'],1)
        self.assertEqual(lookup(db,'Other disease')['resolution_status'],'not_found')
        bad['name']='Other disease';bad['page_title']='Wrong title'
        self.assertEqual(inspect_document(bad,'Other_disease.json')[0]['identity_status'],'page_title_conflict')

    def test_duplicate_payload_only_once_and_same_name_distinct_payloads_ambiguous(self):
        (self.directory/'Synthetic disease.json').write_bytes(self.file.read_bytes())
        db,r = self.build();self.assertEqual((r['indexed_sources'],r['duplicate_file_copies']),(1,1))
        self.assertEqual(r['file_status_counts']['duplicate_content'],1)
        changed = document();changed['data'][0]['value'].append({'key':'Category','value':'other'})
        (self.directory/'Synthetic disease.json').write_text(json.dumps(changed))
        db,r = self.build('different');self.assertEqual(r['indexed_sources'],2)
        self.assertEqual(lookup(db,'Synthetic disease')['resolution_status'],'ambiguous_source')
        self.assertEqual(r['identical_nonempty_target_set_groups'],1)

    def test_empty_and_absent_tables_are_not_negative_evidence(self):
        empty = document('Empty');empty['data']=empty['data'][:1];empty.pop('complete_fetch_summary')
        (self.directory/'Empty.json').write_text(json.dumps(empty))
        db,r = self.build();p=lookup(db,'Empty')['profile']
        self.assertEqual((p['target_count'],p['target_tables']),(0,0));self.assertEqual(p['capture_state'],'not_recorded')
        self.assertEqual(r['sources_without_target_table'],1)
        self.assertEqual(lookup(db,'Unknown')['resolution_status'],'not_found')

    def test_capture_discrepancies_species_and_invalid_rows_are_preserved(self):
        d=document();d['complete_fetch_summary'][0]['actual']=3
        d['data'][1]['value'][0]['value'][0]['Species']='Mus musculus'
        d['data'][1]['value'][0]['value'].append('broken row')
        p,rows=inspect_document(d,self.file.name)
        self.assertEqual(p['capture_state'],'count_mismatch');self.assertEqual(p['unidentified_rows'],2)
        self.assertEqual(p['species_status'],'source_fields_unverified');self.assertEqual(rows[0]['species_fields'],{'Species':'Mus musculus'})
        self.assertIsNone(rows[-1]['uniprot'])
        d=document();d['complete_fetch_summary'][0]['status']='skipped'
        self.assertEqual(inspect_document(d,self.file.name)[0]['capture_state'],'source_reported_skipped')
        d['complete_fetch_summary'].append(copy.deepcopy(d['complete_fetch_summary'][0]))
        self.assertEqual(inspect_document(d,self.file.name)[0]['capture_state'],'ambiguous_summary')

    def test_duplicate_identity_and_malformed_structures_not_indexed(self):
        d=document();d['data'][0]['value'].append({'key':'Disease Name','value':'Another'})
        self.assertEqual(inspect_document(d,self.file.name)[0]['identity_status'],'identity_incomplete')
        d['data'][1]['value'][0]['value']={}
        self.file.write_text(json.dumps(d));db,r=self.build()
        self.assertEqual(r['indexed_sources'],0);self.assertEqual(r['file_status_counts']['invalid_unreadable_or_oversized'],1)

    def test_source_change_invalidates_selected_witness(self):
        db,_=self.build();r=lookup(db,'Synthetic disease');self.file.write_text(json.dumps(document(),indent=2))
        with self.assertRaises(ValueError):verify_witness(self.source,r['profile'],r['rows'][0])

    def test_incomplete_catalog_readonly_and_unknown_schema(self):
        db,_=self.build();con,_=connect(db)
        with self.assertRaises(sqlite3.OperationalError):con.execute('DELETE FROM diseases')
        con.close()
        with sqlite3.connect(db) as con:con.execute("UPDATE metadata SET value='false' WHERE key='build_complete'")
        with self.assertRaises(ValueError):connect(db)
        with self.assertRaises(sqlite3.OperationalError):connect(self.root/'absent.sqlite3')
        self.assertFalse((self.root/'absent.sqlite3').exists())

    def test_existing_and_in_source_outputs_rejected(self):
        self.build()
        with self.assertRaises(FileExistsError):self.build()
        with self.assertRaises(ValueError):build(self.source,self.herbs,self.source/'out','v1')

    def test_changed_snapshot_never_publishes(self):
        from scripts import build_etcm_diseases as mod
        real=mod.read_document
        def changed(*args):
            value=real(*args);self.file.write_bytes(self.file.read_bytes()+b' ');return value
        with patch.object(mod,'read_document',side_effect=changed):
            with self.assertRaises(ValueError):self.build()
        self.assertFalse((self.root/'out/diseases.sqlite3').exists())
        with self.assertRaises(ValueError):connect(self.root/'out/catalog.building.sqlite3')

    def test_external_symlinks_and_oversized_files_are_rejected(self):
        (self.directory/'Outside.json').symlink_to(self.herbs)
        db,r=self.build();self.assertEqual(r['file_status_counts']['invalid_unreadable_or_oversized'],1)
        with patch('tcm.disease_catalog.MAX_DOCUMENT_BYTES',10):
            db,r=self.build('tiny');self.assertEqual(r['indexed_sources'],0)

    def test_lookup_pagination_and_invalid_parameters(self):
        d=document();d['data'][1]['value'][0]['value']*=6
        self.file.write_text(json.dumps(d));db,_=self.build()
        self.assertEqual(len(lookup(db,'Synthetic disease')['rows']),20)
        self.assertTrue(lookup(db,'Synthetic disease')['has_next'])
        self.assertEqual(len(lookup(db,'Synthetic disease',page=2)['rows']),4)
        for page in (True,0,10001,'1'):
            with self.assertRaises(ValueError):lookup(db,'Synthetic disease',page=page)
        self.assertEqual(lookup(db,"' OR 1=1 --")['resolution_status'],'not_found')


if __name__=='__main__':unittest.main()
