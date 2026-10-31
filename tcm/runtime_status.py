"""Identify source drift since application import, without exposing private paths."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fingerprint(root):
    files = {root/name for name in ('app.py', 'config.py', 'features.py')}
    # The container starts uvicorn directly and does not ship the preview CLI.
    files.update(root/name for name in ('source_access.py', 'scripts/preview_local_tasks.py')
                 if (root/name).is_file())
    for folder, suffixes in (('agents', ('.py',)), ('tcm', ('.py', '.js', '.html')),
                             ('templates', ('.html',)), ('assets/web', ('.css', '.js', '.svg', '.json'))):
        files.update(p for p in (root/folder).rglob('*') if p.is_file() and p.suffix in suffixes)
    h = hashlib.sha256()
    for p in sorted(files):
        h.update(p.relative_to(root).as_posix().encode()); h.update(b'\0')
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


class RuntimeStatus:
    def __init__(self, root=ROOT):
        self.root = root
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.source_revision = fingerprint(root)

    def describe(self):
        try:
            disk = fingerprint(self.root)
        except OSError:
            disk = None
        return {'started_at': self.started_at, 'source_revision': self.source_revision,
                'disk_revision': disk,
                'restart_required': disk != self.source_revision if disk is not None else None,
                'source_check': 'ok' if disk is not None else 'unavailable'}
