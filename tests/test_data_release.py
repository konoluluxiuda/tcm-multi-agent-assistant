from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
import sqlite3

from tests.rag_release_fixture import fixture, save
from tcm.data_release import build, check
from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_revisions import fingerprint


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.config=fixture(self.root/'source');self.out=self.root/'release'

    def test_build_and_readback_keep_raw_inputs_unchanged_and_no_model_scores(self):
        before={k:digest(self.config[k]) for k in ('herbs','enrichment','targets','pdf_high','seeds')}
        r=build(self.config,self.out)
        self.assertEqual(r['status'],'ready_research_only')
        self.assertEqual(r['benchmark']['frozen_cases'],3)
        self.assertIsNone(r['benchmark']['retrieval_or_generation_scores'])
        self.assertEqual(r['legacy_vectors']['status'],'not_registered')
        self.assertEqual(check(self.out)['status'],'passed')
        self.assertEqual(before,{k:digest(self.config[k]) for k in before})
        self.assertEqual((self.out/'sources.private.json').stat().st_mode & 0o777,0o600)
        with self.assertRaises(FileExistsError):build(self.config,self.out)

    def test_raw_file_drift_is_rejected_instead_of_rebasing_answers(self):
        build(self.config,self.out)
        p=Path(self.config['source_root'])/'etcm_herbs/Alpha.json'
        p.write_text(p.read_text()+' ')
        with self.assertRaisesRegex(ValueError,'raw herb'):check(self.out)

    def test_benchmark_artifact_mutation_is_detected(self):
        build(self.config,self.out)
        p=self.out/'benchmark_references.json';p.write_text(p.read_text().replace('evidence_found','missing_field',1))
        with self.assertRaisesRegex(ValueError,'artifact changed'):check(self.out)

    def test_mismatched_revision_chain_rejected_before_publication(self):
        p=Path(self.config['revisions'])/'research_view.json';p.write_text(p.read_text()+' ')
        with self.assertRaisesRegex(ValueError,'Revision'):build(self.config,self.out)
        self.assertFalse((self.out/'release.json').exists())

    def test_index_failure_never_publishes_a_ready_release_or_overwrites_sources(self):
        original=digest(Path(self.config['index'])/'research.sqlite3')
        with patch('scripts.build_mineru_retrieval.build',side_effect=OSError('injected')):
            with self.assertRaises(OSError):build(self.config,self.out)
        self.assertFalse((self.out/'release.json').exists())
        self.assertTrue((self.out/'failed.json').exists())
        self.assertEqual(original,digest(Path(self.config['index'])/'research.sqlite3'))

    def test_old_named_mapping_is_quarantined_not_silently_rewritten(self):
        with sqlite3.connect(self.config['enrichment']) as db:
            db.execute("UPDATE name_candidates SET name_zh='旧名字' WHERE entry_id='e-beta'")
        original=digest(self.config['enrichment'])
        r=build(self.config,self.out)
        m=r['sidecars']['mapping']
        self.assertEqual(m['status'],'partially_compatible')
        self.assertEqual(len(m['quarantined_candidates']),1)
        self.assertFalse(m['identity_verified'])
        self.assertEqual(original,digest(self.config['enrichment']))

    def test_legacy_vector_receipt_never_becomes_an_enabled_current_index(self):
        dense=self.root/'dense';dense.mkdir();(dense/'vectors.npy').write_bytes(b'fixture-only')
        save(dense/'items.json',[])
        save(dense/'manifest.json',{'index_sha256':'0'*64,'count':0,'dimension':512,
            'model_fingerprint':'fixture-model','recipe':{'max_tokens':512},
            'vectors_sha256':digest(dense/'vectors.npy'),'items_sha256':digest(dense/'items.json')})
        r=build({**self.config,'dense_cache':str(dense)},self.out)
        self.assertEqual(r['legacy_vectors']['status'],'incompatible_source_index')
        self.assertFalse(r['legacy_vectors']['eligible_for_current_release'])
        self.assertEqual(r['vector_builds'],0)

    def test_output_cannot_be_created_inside_source_tree(self):
        with self.assertRaises(ValueError):build(self.config,Path(self.config['source_root'])/'release')

    def test_manifest_counts_cannot_change_without_changing_content_identity(self):
        build(self.config,self.out)
        p=self.out/'release.json';r=json.loads(p.read_text());r['benchmark']['frozen_cases']=999;save(p,r)
        with self.assertRaisesRegex(ValueError,'content identity'):check(self.out)

    def test_uncheckpointed_wal_is_not_ignored_by_file_hash_checks(self):
        build(self.config,self.out)
        Path(self.config['herbs']+'-wal').write_bytes(b'uncheckpointed-source')
        with self.assertRaisesRegex(ValueError,'WAL'):check(self.out)

    def test_normalized_value_corruption_is_not_blessed_by_correct_raw_pointer(self):
        with sqlite3.connect(self.config['herbs']) as db:
            row=db.execute('SELECT source_record_id,document FROM records LIMIT 1').fetchone()
            doc=json.loads(row[1]);doc['fields']['medicinal_part']['value']='invented'
            db.execute('UPDATE records SET document=? WHERE source_record_id=?',(json.dumps(doc),row[0]))
        with self.assertRaisesRegex(ValueError,'normalization'):build(self.config,self.out)

    def test_json_reference_outside_registered_snapshot_is_rejected(self):
        import hashlib
        relative='etcm_herbs/Outside.json';p=Path(self.config['source_root'])/relative
        save(p,{'value':'not imported'})
        sid='etcm2-local:'+hashlib.sha256(relative.encode()).hexdigest()
        seed=json.loads(Path(self.config['seeds']).read_text())
        seed['cases'][0]['relevant_anchors'].append({'kind':'etcm_json','source_record_id':sid,
            'label':'name_pinyin','relative_file_path':relative,'file_sha256':digest(p),
            'json_pointer':'/value','value_sha256':fingerprint('not imported')})
        seed['cases'][0]['group_ids'].append(sid);save(self.config['seeds'],seed)
        with self.assertRaisesRegex(ValueError,'absent from registered'):build(self.config,self.out)

    def test_mapping_built_from_different_raw_blocks_requires_a_new_catalog(self):
        with sqlite3.connect(self.config['enrichment']) as db:
            db.execute("UPDATE metadata SET value=? WHERE key='pharma_blocks_sha256'",(json.dumps('old-blocks'),))
        with self.assertRaisesRegex(ValueError,'Enrichment'):build(self.config,self.out)


if __name__=='__main__':unittest.main()
