import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from tests.large_observation_fixture import fixture,MARKER
from experiments.architecture_candidates.evidence_store import EvidenceStore,Scope,EvidenceConflict


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/'evidence.sqlite3';self.store=EvidenceStore(self.path)
        self.scope=Scope('owner','run','context-synthetic-v1','synthetic')
        self.payload=fixture(14000)

    def put(self):return self.store.put(self.scope,'obs-1',self.payload)

    def test_large_payload_preserved_and_small_index(self):
        ref=self.put();_,_,loaded=self.store.load(self.scope,ref['evidence_handle'])
        self.assertEqual(loaded,self.payload)
        self.assertLess(len(json.dumps(ref).encode()),2000)
        r=self.store.read(self.scope,ref['evidence_handle'],field='target_ids',offset=7000,limit=1)
        self.assertEqual(r['data']['items'],[MARKER]);self.assertEqual(r['data']['source_evidence_id'],'obs-1')
        self.assertEqual(r['data']['source_binding']['dataset_version'],self.scope.dataset_version)

    def test_owner_and_run_isolation(self):
        ref=self.put()
        for s in (replace(self.scope,owner='other'),replace(self.scope,run_id='other')):
            with self.assertRaises(KeyError):self.store.load(s,ref['evidence_handle'])

    def test_source_version_change(self):
        ref=self.put()
        for key in ('dataset_version','catalog_revision'):
            with self.assertRaises(EvidenceConflict):
                self.store.load(replace(self.scope,**{key:'new'}),ref['evidence_handle'])

    def test_unknown_or_path_handle(self):
        for handle in ('0'*32,'../../private','/tmp/data',''):
            with self.assertRaises(KeyError):self.store.load(self.scope,handle)

    def test_same_id_is_idempotent_but_immutable(self):
        self.assertEqual(self.put(),self.put())
        self.payload['data']['target_ids'][0]='CHANGED'
        with self.assertRaises(EvidenceConflict):self.put()

    def test_corruption_is_detected(self):
        ref=self.put()
        with sqlite3.connect(self.path) as db:db.execute("UPDATE evidence SET payload='{}'")
        with self.assertRaises(EvidenceConflict):self.store.load(self.scope,ref['evidence_handle'])

    def test_failure_returns_no_handle_and_no_row(self):
        with patch('experiments.architecture_candidates.evidence_store.uuid4',side_effect=OSError('disk fault fixture')):
            with self.assertRaises(OSError):self.put()
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM evidence').fetchone()[0],0)

    def test_restart_instance(self):
        ref=self.put();new=EvidenceStore(self.path)
        self.assertEqual(new.load(self.scope,ref['evidence_handle'])[2],self.payload)

    def test_read_bounds_and_missing_field(self):
        ref=self.put()
        for kwargs in ({'field':'target_ids','limit':21},{'field':'target_ids','offset':-1}):
            with self.assertRaises(ValueError):self.store.read(self.scope,ref['evidence_handle'],**kwargs)
        self.assertEqual(self.store.read(self.scope,ref['evidence_handle'],field='items')['status'],'error')

    def test_oversized_storage_rejected(self):
        self.store.MAX_BYTES=100
        with self.assertRaises(EvidenceConflict):self.put()

    def test_wrong_database_rejected(self):
        path=self.path.with_name('other.sqlite3')
        with sqlite3.connect(path) as db:db.execute('CREATE TABLE unrelated(x)')
        with self.assertRaises(EvidenceConflict):EvidenceStore(path)

    def test_symlink_rejected(self):
        path=self.path.with_name('link');path.symlink_to(self.path)
        with self.assertRaises(OSError):EvidenceStore(path)

    def test_database_insert_failure_rolls_back(self):
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON evidence BEGIN SELECT RAISE(ABORT,'write failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.put()
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM evidence').fetchone()[0],0)
