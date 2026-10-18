"""Online WAL snapshots, signing-key continuity and isolated restoration contracts."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from agents.conversation_store import ConversationConflict, ConversationStore, SessionExpired
from agents.memory_backup import BackupError, create_backup, inspect_backup, restore_backup


class MemoryBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source.sqlite3'
        self.bundle = self.root/'bundle'
        self.target = self.root/'restore/memory.sqlite3'
        self.now = 1000000
        self.store = ConversationStore(self.source, ttl_seconds=100, clock=lambda: self.now)
        self.cookie = self.store.new_cookie()
        self.messages = [{'type': 'human', 'data': {'content': 'private fixture message'}}]
        self.store.save_serialized(self.cookie, self.messages, {'kind': 'fixture'}, revision=0)

    def backup(self):
        return create_backup(self.source, self.bundle, ttl_seconds=100, clock=lambda: self.now)

    def restored(self):
        restore_backup(self.bundle, self.target, clock=lambda: self.now)
        return ConversationStore(self.target, ttl_seconds=100, clock=lambda: self.now)

    def test_old_cookie_and_payload_survive_new_process_without_secret_output(self):
        manifest = self.backup()
        restored = self.restored()
        self.assertTrue(restored.valid_cookie(self.cookie))
        snapshot = restored.load_serialized(self.cookie)
        self.assertEqual(snapshot.messages, self.messages)
        self.assertEqual(snapshot.tcm_context, {'kind': 'fixture'})
        self.assertEqual(snapshot.revision, 1)
        with self.assertRaises(ConversationConflict):
            restored.save_serialized(self.cookie, [], None, revision=0)
        code = ('from agents.conversation_store import ConversationStore; import sys; '
                's=ConversationStore(sys.argv[1],ttl_seconds=100,clock=lambda:1000000); '
                'assert s.valid_cookie(sys.stdin.read()); print("cookie_ok")')
        proc = subprocess.run([sys.executable, '-c', code, str(self.target)], input=self.cookie,
                              text=True, capture_output=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), 'cookie_ok')
        self.assertNotIn(self.cookie, json.dumps(manifest))
        self.assertNotIn('private fixture', json.dumps(manifest))

    def test_deleted_tombstone_survives_restore_and_blocks_inflight_write(self):
        self.store.delete(self.cookie)
        self.backup(); restored = self.restored()
        with self.assertRaises(SessionExpired): restored.load_serialized(self.cookie)
        with self.assertRaises(ConversationConflict):
            restored.save_serialized(self.cookie, self.messages, None, revision=1)

    def test_expired_payload_removed_only_in_backup_not_live_source(self):
        self.now += 101
        manifest = self.backup()
        self.assertEqual(manifest['expired_payloads_removed'], 1)
        with sqlite3.connect(self.source) as db:
            self.assertIn('private fixture', db.execute('SELECT payload FROM conversations').fetchone()[0])
        with sqlite3.connect(self.bundle/'memory.sqlite3') as db:
            self.assertEqual(db.execute('SELECT payload,deleted FROM conversations').fetchone(), ('{}',1))

    def test_elapsed_time_does_not_extend_session_retention(self):
        self.backup(); self.now += 101
        result = restore_backup(self.bundle, self.target, clock=lambda: self.now)
        self.assertEqual(result['expired_payloads_removed'], 1)
        restored = ConversationStore(self.target, ttl_seconds=100, clock=lambda: self.now)
        with self.assertRaises(SessionExpired): restored.load_serialized(self.cookie)

    def test_committed_wal_included_uncommitted_writer_excluded(self):
        writer = sqlite3.connect(self.source, isolation_level=None)
        self.addCleanup(writer.close)
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        original = writer.execute('SELECT payload FROM conversations').fetchone()[0]
        committed = json.loads(original)
        committed['messages'][0]['data']['content'] = 'committed WAL fixture'
        writer.execute('UPDATE conversations SET payload=?', (json.dumps(committed),))
        self.assertGreater(Path(str(self.source)+'-wal').stat().st_size, 0)
        writer.execute('BEGIN IMMEDIATE')
        writer.execute('UPDATE conversations SET payload=?', (original,))
        before = self.source.read_bytes(), Path(str(self.source)+'-wal').read_bytes()
        self.backup()
        after = self.source.read_bytes(), Path(str(self.source)+'-wal').read_bytes()
        self.assertEqual(before, after)
        writer.rollback()
        restored = self.restored()
        self.assertEqual(restored.load_serialized(self.cookie).messages, committed['messages'])

    def test_existing_source_target_bundle_and_sidecars_never_overwritten(self):
        self.backup()
        source_before = self.source.read_bytes()
        with self.assertRaises(BackupError): restore_backup(self.bundle, self.source, clock=lambda:self.now)
        self.assertEqual(self.source.read_bytes(), source_before)
        with self.assertRaises(BackupError): self.backup()
        self.target.parent.mkdir()
        orphan = Path(str(self.target)+'-wal'); orphan.write_bytes(b'keep')
        with self.assertRaises(BackupError): self.restored()
        self.assertEqual(orphan.read_bytes(), b'keep')
        self.assertFalse(self.target.exists())

    def test_corruption_rejected_before_target_creation(self):
        self.backup()
        with (self.bundle/'memory.sqlite3').open('ab') as stream: stream.write(b'tamper')
        with self.assertRaisesRegex(BackupError, 'checksum_mismatch'): self.restored()
        self.assertFalse(self.target.exists())

    def test_expired_backup_refused_and_inspect_still_works(self):
        self.backup(); self.now += 7*86400
        self.assertTrue(inspect_backup(self.bundle, clock=lambda:self.now)['expired'])
        with self.assertRaisesRegex(BackupError,'backup_expired'): self.restored()

    def test_missing_wrong_or_future_source_is_not_initialized(self):
        missing = self.root/'missing.sqlite3'
        with self.assertRaises(BackupError):
            create_backup(missing,self.bundle)
        self.assertFalse(missing.exists()); self.assertFalse(self.bundle.exists())
        with sqlite3.connect(self.source) as db: db.execute('PRAGMA user_version=99')
        with self.assertRaises(BackupError): self.backup()
        self.assertFalse(self.bundle.exists())
        with sqlite3.connect(self.source) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],99)

    def test_missing_key_or_invalid_payload_rejected(self):
        with sqlite3.connect(self.source) as db:
            db.execute("DELETE FROM settings WHERE key='cookie_secret'")
        with self.assertRaisesRegex(BackupError,'missing_signing_key'): self.backup()
        self.store.new_cookie()
        with sqlite3.connect(self.source) as db: db.execute("UPDATE conversations SET payload='{}'")
        with self.assertRaisesRegex(BackupError,'invalid_conversation_payload'): self.backup()

    def test_private_permissions_and_symlink_refusal(self):
        self.backup(); self.restored()
        self.assertEqual(self.bundle.stat().st_mode & 0o777,0o700)
        for path in (self.bundle/'memory.sqlite3',self.bundle/'manifest.json',self.target):
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
        link = self.root/'linked-bundle'; link.symlink_to(self.bundle)
        with self.assertRaisesRegex(BackupError,'symlink_bundle_rejected'): inspect_backup(link)

    def test_sqlite_internal_statistics_do_not_look_like_foreign_tables(self):
        with sqlite3.connect(self.source) as db: db.execute('ANALYZE')
        self.backup()
        self.assertTrue(self.restored().valid_cookie(self.cookie))

    def test_cli_errors_are_codes_not_private_paths(self):
        proc = subprocess.run([sys.executable,'-m','scripts.backup_memory','backup',
                               '--source',str(self.root/'private-source-missing'),
                               '--out',str(self.bundle)],text=True,capture_output=True,timeout=10)
        self.assertEqual(proc.returncode,2)
        self.assertEqual(json.loads(proc.stdout)['status'],'error')
        self.assertNotIn('private-source-missing',proc.stdout+proc.stderr)


if __name__ == '__main__':
    unittest.main()
