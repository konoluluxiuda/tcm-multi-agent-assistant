"""Offline bundle boundaries and session/project/job continuity."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from agents.conversation_store import ConversationStore
from tcm.project_memory import ProjectStore,ProjectConflict
from tcm.project_semantic import owner_key
from tcm.research_jobs import JobStore
from tcm.runtime_backup import create_bundle,inspect_bundle,restore_bundle,RuntimeBackupError,digest,pg_env


class RuntimeBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.files={r:self.root/(r+'.sqlite3') for r in ('memory','projects','jobs')}
        self.memory=ConversationStore(self.files['memory']);self.cookie=self.memory.new_cookie();self.owner=owner_key(self.cookie)
        self.memory.save_serialized(self.cookie,[{'type':'human','data':{'content':'synthetic private'}}],{'kind':'fixture'},revision=0)
        self.projects=ProjectStore(self.files['projects'])
        self.project=self.projects.save(self.owner,{'title':'研究','herbs':['a','b'],'question':'功能'})
        self.jobs=JobStore(self.files['jobs']);self.job,_=self.jobs.enqueue(self.owner,'功能','abcdefgh')
        _,doc=self.jobs.claim('w');self.jobs.save(self.owner,doc,'w',status='complete',result={'status':'complete','source':'/fixture/1'})
        self.bundle=self.root/'bundle'

    def backup(self):return create_bundle(self.files,self.bundle,workers_stopped=True,cookie_name='fixture_cookie')

    def test_restore_identity_versions_results_and_private_files(self):
        original={r:digest(p) for r,p in self.files.items()};manifest=self.backup()
        target=self.root/'restore';result=restore_bundle(self.bundle,target)
        memory=ConversationStore(target/'memory.sqlite3');self.assertTrue(memory.valid_cookie(self.cookie))
        self.assertEqual(memory.load_serialized(self.cookie).revision,1)
        projects=ProjectStore(target/'projects.sqlite3');self.assertEqual(projects.get(self.owner,self.project['id']),self.project)
        with self.assertRaises(KeyError):projects.get('different-owner',self.project['id'])
        with self.assertRaises(ProjectConflict):projects.save(self.owner,{'title':'x','herbs':['a'],'question':'q'},identifier=self.project['id'],revision=0)
        self.assertEqual(JobStore(target/'jobs.sqlite3').get(self.owner,self.job['id'])['result']['source'],'/fixture/1')
        self.assertEqual(original,{r:digest(p) for r,p in self.files.items()})
        self.assertEqual(result['cookie_name'],'fixture_cookie')
        self.assertNotIn(self.cookie,json.dumps(manifest));self.assertNotIn('synthetic private',json.dumps(manifest))
        for path in [self.bundle,target]:
            self.assertEqual(path.stat().st_mode&0o777,0o700)
            for child in path.iterdir():
                if child.is_file():self.assertEqual(child.stat().st_mode&0o777,0o600)

    def test_active_jobs_and_missing_stop_declaration_rejected(self):
        with self.assertRaises(RuntimeBackupError):create_bundle(self.files,self.bundle)
        self.jobs.enqueue('other','q','newrequest')
        with self.assertRaisesRegex(RuntimeBackupError,'active_jobs'):self.backup()
        self.assertFalse(self.bundle.exists())

    def test_existing_target_is_never_overwritten(self):
        self.backup();target=self.root/'existing';target.mkdir();(target/'keep').write_text('keep')
        with self.assertRaises(RuntimeBackupError):restore_bundle(self.bundle,target)
        self.assertEqual((target/'keep').read_text(),'keep')
        with self.assertRaises(RuntimeBackupError):self.backup()
        inspect_bundle(self.bundle)

    def test_checksum_and_path_traversal_rejected(self):
        self.backup();manifest=json.loads((self.bundle/'manifest.json').read_text())
        changed=dict(manifest);changed['files']={**manifest['files'],'../escape':{}}
        (self.bundle/'manifest.json').write_text(json.dumps(changed))
        with self.assertRaises(RuntimeBackupError):inspect_bundle(self.bundle)
        (self.bundle/'manifest.json').write_text(json.dumps(manifest))
        with (self.bundle/'projects.sqlite3').open('ab') as stream:stream.write(b'tamper')
        with self.assertRaisesRegex(RuntimeBackupError,'checksum'):restore_bundle(self.bundle,self.root/'bad')
        self.assertFalse((self.root/'bad').exists())

    def test_committed_wal_copied_and_wrong_database_rejected(self):
        writer=sqlite3.connect(self.files['projects']);self.addCleanup(writer.close)
        writer.execute('PRAGMA journal_mode=WAL');writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute('UPDATE projects SET revision=2');writer.commit()
        self.backup();restore_bundle(self.bundle,self.root/'wal')
        self.assertEqual(ProjectStore(self.root/'wal/projects.sqlite3').get(self.owner,self.project['id'])['revision'],2)
        with self.assertRaises(RuntimeBackupError):create_bundle({**self.files,'projects':self.files['memory']},self.root/'wrong',workers_stopped=True)

    def test_dump_credentials_not_inherited_or_emitted(self):
        with patch.dict(os.environ,{'PGPASSWORD':'unrelated','CHAT_API_KEY':'never-pass','PGHOST':'other'}):
            env=pg_env('host=/tmp/local user=test dbname=fixture password=synthetic')
        self.assertEqual(env['PGPASSWORD'],'synthetic');self.assertEqual(env['PGHOST'],'/tmp/local')
        self.assertNotIn('CHAT_API_KEY',env)

    def test_symlink_payload_and_missing_source_rejected_without_creation(self):
        self.backup()
        (self.bundle/'projects.sqlite3').unlink()
        (self.bundle/'projects.sqlite3').symlink_to(self.files['projects'])
        with self.assertRaises(RuntimeBackupError):inspect_bundle(self.bundle)
        missing=self.root/'missing.sqlite3'
        with self.assertRaises(RuntimeBackupError):create_bundle({**self.files,'projects':missing},self.root/'missing-bundle',workers_stopped=True)
        self.assertFalse(missing.exists());self.assertFalse((self.root/'missing-bundle').exists())

    def test_inspection_rejects_invalid_identity_configuration_before_restore(self):
        self.backup();path=self.bundle/'manifest.json';manifest=json.loads(path.read_text())
        manifest['cookie_name']='bad;cookie';path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(RuntimeBackupError,'invalid_cookie_name'):restore_bundle(self.bundle,self.root/'bad-identity')
        self.assertFalse((self.root/'bad-identity').exists())
