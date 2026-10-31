"""Identify source drift since application import, without exposing private paths."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fingerprint(root):
    files = {root/name for name in ('app.py', 'config.py', 'features.py')}
    # Comparable application bytes; deployment entrypoints are tracked below.
    files.update(root/name for name in ('source_access.py',)
                 if (root/name).is_file())
    # Match the research image's agent allowlist. The host may additionally
    # retain optional legacy image/RAG/web modules, tracked separately below.
    files.update((root/'agents').glob('*.py'))
    files.update((root/'agents/guardrails').glob('*.py'))
    for folder, suffixes in (('tcm', ('.py', '.js', '.html')),
                             ('templates', ('.html',)), ('assets/web', ('.css', '.js', '.svg', '.json'))):
        files.update(p for p in (root/folder).rglob('*') if p.is_file() and p.suffix in suffixes)
    h = hashlib.sha256()
    for p in sorted(files):
        h.update(p.relative_to(root).as_posix().encode()); h.update(b'\0')
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def entrypoint_fingerprint(root):
    h = hashlib.sha256()
    for name in ('scripts/preview_local_tasks.py',):
        p = root/name
        h.update(name.encode()); h.update(b'\0')
        h.update(hashlib.sha256(p.read_bytes()).digest() if p.is_file() else b'absent')
    return h.hexdigest()


def optional_modules_fingerprint(root):
    h=hashlib.sha256()
    for p in sorted((root/'agents').rglob('*.py')):
        if p.parent in (root/'agents',root/'agents/guardrails'):continue
        h.update(p.relative_to(root).as_posix().encode());h.update(b'\0')
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


class RuntimeStatus:
    def __init__(self, root=ROOT):
        self.root = root
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.source_revision = fingerprint(root)
        self.entrypoint_revision = entrypoint_fingerprint(root)
        self.optional_modules_revision = optional_modules_fingerprint(root)

    def describe(self):
        try:
            disk = fingerprint(self.root)
            entrypoint_disk = entrypoint_fingerprint(self.root)
            optional_disk = optional_modules_fingerprint(self.root)
        except OSError:
            disk = None
            entrypoint_disk = None
            optional_disk = None
        return {'started_at': self.started_at, 'source_revision': self.source_revision,
                'disk_revision': disk,
                'source_revision_scope': 'research-application-v3',
                'entrypoint_revision': self.entrypoint_revision, 'disk_entrypoint_revision': entrypoint_disk,
                'optional_modules_revision':self.optional_modules_revision,'disk_optional_modules_revision':optional_disk,
                'restart_required': (disk != self.source_revision or entrypoint_disk != self.entrypoint_revision or optional_disk != self.optional_modules_revision) if disk is not None else None,
                'source_check': 'ok' if disk is not None else 'unavailable'}
