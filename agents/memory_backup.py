"""Private SQLite online snapshots; restoration is only allowed to a new path."""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time

FORMAT = 'tcm_conversation_backup_v1'


class BackupError(RuntimeError):
    """Public error code; never contains messages, credentials or private paths."""


def _digest(path):
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


def _readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
    db.execute('PRAGMA query_only=ON')
    db.execute('PRAGMA trusted_schema=OFF')
    return db


def _validate(db):
    if db.execute('PRAGMA user_version').fetchone() != (1,):
        raise BackupError('unsupported_schema')
    if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise BackupError('invalid_database')
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
              if not row[0].startswith('sqlite_')}
    if tables != {'settings', 'conversations'}:
        raise BackupError('not_conversation_database')
    if db.execute("SELECT value FROM settings WHERE key='store_kind'").fetchone() != ('tcm_completed_conversations_v1',):
        raise BackupError('not_conversation_database')
    secret = db.execute("SELECT value FROM settings WHERE key='cookie_secret'").fetchone()
    if not secret or not re.fullmatch('[0-9a-f]{64}', secret[0]):
        raise BackupError('missing_signing_key')
    for key, revision, raw, updated, deleted in db.execute('SELECT key,revision,payload,updated_at,deleted FROM conversations'):
        if (not re.fullmatch('[0-9a-f]{64}', key) or type(revision) is not int or revision < 1 or
                not isinstance(updated, (float, int)) or not math.isfinite(updated) or deleted not in (0, 1)):
            raise BackupError('invalid_conversation_payload')
        obj = json.loads(raw)
        if deleted:
            if obj != {}:
                raise BackupError('invalid_conversation_payload')
        elif (not isinstance(obj, dict) or obj.get('version') != 1 or
              not isinstance(obj.get('messages'), list) or
              any(not isinstance(message, dict) for message in obj['messages']) or
              ('tcm_context' not in obj) or (obj['tcm_context'] is not None and not isinstance(obj['tcm_context'], dict))):
            raise BackupError('invalid_conversation_payload')


def _purge(db, ttl_seconds, now):
    db.execute('PRAGMA secure_delete=ON')
    with db:
        count = db.execute("UPDATE conversations SET payload='{}',deleted=1,revision=revision+1 "
                           'WHERE deleted=0 AND updated_at<=?', (now - ttl_seconds,)).rowcount
    return count


def _copy(source, target, ttl_seconds, now):
    """Destination is exclusively created; committed WAL data comes via backup()."""
    created = False
    src = dest = None
    try:
        src = _readonly(source)
        _validate(src)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd); created = True
        dest = sqlite3.connect(target, timeout=10)
        started = time.monotonic()
        def progress(status, remaining, total):
            if time.monotonic() - started > 30:
                raise BackupError('snapshot_timeout')
        src.backup(dest, pages=256, progress=progress, sleep=.05)
        dest.execute('PRAGMA journal_mode=DELETE')
        _validate(dest)
        expired = _purge(dest, ttl_seconds, now)
        # Remove freed expired payload pages from the portable snapshot too.
        dest.execute('VACUUM')
        _validate(dest)
        dest.close(); dest = None
        with target.open('rb') as stream:
            os.fsync(stream.fileno())
        return expired
    except Exception:
        if dest is not None:
            dest.close(); dest = None
        if created:
            for suffix in ('', '-wal', '-shm', '-journal'):
                Path(str(target) + suffix).unlink(missing_ok=True)
        raise
    finally:
        if src is not None: src.close()
        if dest is not None: dest.close()


def _timestamp(now):
    return datetime.fromtimestamp(now, timezone.utc).isoformat()


def create_backup(source, output, *, ttl_seconds=30*86400, retention_days=7, clock=time.time):
    if type(ttl_seconds) is not int or not 0 < ttl_seconds < 2**63 or type(retention_days) is not int or not 1 <= retention_days <= 30:
        raise BackupError('invalid_retention')
    output = Path(output).absolute()
    owned = False
    try:
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        output.mkdir(mode=0o700); owned = True
        now = clock()
        expired = _copy(Path(source), output/'memory.sqlite3', ttl_seconds, now)
        manifest = {'format': FORMAT, 'schema_version': 1, 'database_file': 'memory.sqlite3',
                    'created_at': _timestamp(now), 'created_at_unix': now,
                    'expires_at': _timestamp(now + retention_days*86400),
                    'expires_at_unix': now + retention_days*86400,
                    'ttl_seconds': ttl_seconds, 'retention_days': retention_days,
                    'database_sha256': _digest(output/'memory.sqlite3'),
                    'expired_payloads_removed': expired}
        fd = os.open(output/'manifest.json', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(manifest, stream, indent=2); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        return manifest
    except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError, BackupError) as exc:
        if owned: shutil.rmtree(output)
        raise BackupError(str(exc) if isinstance(exc, BackupError) else 'backup_failed') from None


def inspect_backup(bundle, *, clock=time.time):
    bundle = Path(bundle).absolute()
    try:
        manifest_path, database = bundle/'manifest.json', bundle/'memory.sqlite3'
        if bundle.is_symlink() or manifest_path.is_symlink() or database.is_symlink():
            raise BackupError('symlink_bundle_rejected')
        if manifest_path.stat().st_size > 65536:
            raise BackupError('invalid_manifest')
        manifest = json.loads(manifest_path.read_text())
        if (manifest['format'] != FORMAT or manifest['schema_version'] != 1 or
                manifest['database_file'] != 'memory.sqlite3' or type(manifest['ttl_seconds']) is not int or
                not 0 < manifest['ttl_seconds'] < 2**63 or type(manifest['retention_days']) is not int or
                not 1 <= manifest['retention_days'] <= 30 or
                not math.isfinite(manifest['created_at_unix']) or not math.isfinite(manifest['expires_at_unix']) or
                manifest['expires_at_unix'] != manifest['created_at_unix'] + manifest['retention_days']*86400):
            raise BackupError('invalid_manifest')
        if _digest(database) != manifest['database_sha256']:
            raise BackupError('checksum_mismatch')
        db = _readonly(database)
        try: _validate(db)
        finally: db.close()
        return dict(manifest, expired=clock() >= manifest['expires_at_unix'])
    except (OSError, sqlite3.Error, KeyError, ValueError, TypeError, OverflowError, BackupError) as exc:
        raise BackupError(str(exc) if isinstance(exc, BackupError) else 'invalid_backup') from None


def restore_backup(bundle, target, *, clock=time.time):
    manifest = inspect_backup(bundle, clock=clock)
    if manifest['expired']:
        raise BackupError('backup_expired')
    target = Path(target).absolute()
    try:
        # Do not remove/overwrite a live DB, broken symlink or orphan WAL files.
        if any(os.path.lexists(str(target) + suffix) for suffix in ('', '-wal', '-shm', '-journal')):
            raise BackupError('target_exists')
        removed = _copy(Path(bundle)/'memory.sqlite3', target, manifest['ttl_seconds'], clock())
        return {'status': 'restored', 'schema_version': 1, 'ttl_seconds': manifest['ttl_seconds'],
                'database_sha256': _digest(target), 'expired_payloads_removed': removed}
    except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError, BackupError) as exc:
        raise BackupError(str(exc) if isinstance(exc, BackupError) else 'restore_failed') from None
