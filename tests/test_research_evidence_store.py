"""Production evidence quotas, binding, transactional publication and maintenance."""
from dataclasses import replace
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from tests.large_observation_fixture import fixture,MARKER
from tcm.research_evidence_store import EvidencePolicy,EvidenceStore,RunEvidence,EvidenceConflict,canonical


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'evidence.sqlite3'
        self.session=RunEvidence(self.path,'alice','run-a')
        self.payload=fixture(14000)

    def test_complete_payload_survives_restart_but_index_is_small(self):
        ref=self.session.prepare('obs-1',self.payload)
        self.assertTrue(ref['externalized']);self.assertLess(len(canonical(ref)),12000)
        self.assertEqual(ref['field_counts']['target_ids'],14000)
        self.assertEqual(RunEvidence(self.path,'alice','run-a').resolve('obs-1',ref),self.payload)
        self.assertEqual(self.session.prepare('obs-1',self.payload),ref)
        changed=copy.deepcopy(self.payload);changed['data']['target_ids'][0]='new'
        with self.assertRaises(EvidenceConflict):self.session.prepare('obs-1',changed)

    def test_owner_run_id_handle_digest_and_source_versions_are_checked(self):
        ref=self.session.prepare('obs-1',self.payload)
        for owner,run in (('bob','run-a'),('alice','run-b')):
            with self.assertRaises(EvidenceConflict):RunEvidence(self.path,owner,run).resolve('obs-1',ref)
        for key,value in [('evidence_handle','../secret'),('source_evidence_id','obs-2'),
                          ('content_sha256','0'*64),('source_binding',{})]:
            with self.assertRaises(EvidenceConflict):self.session.resolve('obs-1',{**ref,key:value})
        with self.assertRaises(EvidenceConflict):self.session.resolve('obs-2',ref)

    def test_corruption_and_missing_payload_do_not_return_cached_preview(self):
        ref=self.session.prepare('obs-1',self.payload)
        with sqlite3.connect(self.path) as db:db.execute("UPDATE evidence SET payload='{}'")
        with self.assertRaises(EvidenceConflict):self.session.resolve('obs-1',ref)
        with sqlite3.connect(self.path) as db:db.execute('DELETE FROM evidence')
        with self.assertRaises(EvidenceConflict):self.session.resolve('obs-1',ref)

    def test_run_owner_and_total_quotas_are_atomic(self):
        size=len(canonical(self.payload).encode())
        for quota in ('run_bytes','owner_bytes','total_bytes'):
            path=self.path.with_name(quota+'.sqlite3')
            policy=EvidencePolicy(**{quota:size+10})
            s=RunEvidence(path,'alice','run-a',policy=policy)
            s.prepare('obs-1',self.payload)
            other=s if quota=='run_bytes' else RunEvidence(path,'alice' if quota=='owner_bytes' else 'bob','run-b',policy=policy)
            with self.assertRaises(EvidenceConflict):other.prepare('obs-2',self.payload)
            with sqlite3.connect(path) as db:self.assertEqual(db.execute('SELECT count(*) FROM evidence').fetchone()[0],1)

    def test_actual_sqlite_failure_never_publishes_a_reference(self):
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TRIGGER fail BEFORE INSERT ON evidence BEGIN SELECT RAISE(ABORT,'disk fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.session.prepare('obs-1',self.payload)
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM evidence').fetchone()[0],0)
        with self.assertRaises(EvidenceConflict):
            RunEvidence(self.path,'alice','run',policy=EvidencePolicy(payload_bytes=1000)).prepare('obs-1',self.payload)

    def test_maintenance_refuses_live_reads_and_wrong_database(self):
        self.session.prepare('obs-1',self.payload)
        with self.session.store.activity():
            with self.assertRaises(EvidenceConflict):self.session.store.delete_run('alice','run-a')
        self.session.store.delete_run('alice','run-a')
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM evidence').fetchone()[0],0)
        wrong=self.path.with_name('wrong.sqlite3')
        with sqlite3.connect(wrong) as db:db.execute('CREATE TABLE private(x)')
        with self.assertRaises(EvidenceConflict):EvidenceStore(wrong)
        link=self.path.with_name('link');link.symlink_to(self.path)
        with self.assertRaises(OSError):EvidenceStore(link)
