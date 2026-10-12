"""Hash-verified public source allowlist and bounded anonymous-session artifacts."""
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from agents.source_links import local_source_path, source_id

MAX_SOURCE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class Asset:
    content: bytes
    media_type: str
    filename: str


def file_asset(root, path, expected_sha256):
    candidate = root / path
    # Reject every symlink, even if it resolves to another file inside the tree.
    if candidate.resolve() != candidate or not candidate.is_file():
        return None
    if candidate.stat().st_size > MAX_SOURCE_BYTES:
        return None
    try:
        with candidate.open('rb') as stream:
            raw = stream.read(MAX_SOURCE_BYTES + 1)
    except OSError:
        return None
    if len(raw) > MAX_SOURCE_BYTES or hashlib.sha256(raw).hexdigest() != expected_sha256:
        return None
    suffix = candidate.suffix.lower()
    if suffix == '.pdf' and raw.startswith(b'%PDF-'):
        media = 'application/pdf'
    elif suffix == '.png' and raw.startswith(b'\x89PNG\r\n\x1a\n'):
        media = 'image/png'
    else:
        return None
    # Serve these exact verified bytes; never reopen a path after verification.
    return Asset(raw, media, candidate.name)


class SourceCatalog:
    def __init__(self, root, manifest):
        self.root = Path(root).resolve()
        self.entries = {}
        manifest = Path(manifest)
        if not manifest.is_file():
            return  # Default-deny when no allowlist is supplied.
        payload = json.loads(manifest.read_text())
        if payload.get('version') != 1 or not isinstance(payload.get('sources'), dict):
            raise ValueError('Invalid source allowlist')
        for identifier, entry in payload['sources'].items():
            path = local_source_path(entry['path'])
            if path != entry['path'] or source_id(path) != identifier:
                raise ValueError('Invalid source identity')
            folder, suffix = Path(path).parts[1], Path(path).suffix.lower()
            if not (folder in ('raw','raw_extras') and suffix == '.pdf' or folder == 'parsed_docs' and suffix == '.png'):
                raise ValueError('Unsupported published source type')
            if not isinstance(entry['sha256'], str) or len(entry['sha256']) != 64:
                raise ValueError('Invalid source hash')
            self.entries[identifier] = dict(entry)

    def read(self, identifier):
        entry = self.entries.get(identifier)
        return file_asset(self.root, entry['path'], entry['sha256']) if entry else None

    def legacy_id(self, value):
        path = local_source_path(value)
        identifier = source_id(path) if path else None
        return identifier if identifier in self.entries else None


class PrivateArtifacts:
    """Cookies + random URLs scope access; no account-level identity is claimed."""
    def __init__(self, *, ttl=600, max_entries=128, max_bytes=64 * 1024 * 1024, clock=time.monotonic):
        if ttl <= 0 or max_entries < 1 or max_bytes < 1:
            raise ValueError("Artifact limits must be positive")
        self.ttl, self.max_entries, self.max_bytes, self.clock = ttl, max_entries, max_bytes, clock
        self.entries = OrderedDict()
        self.bytes = 0
        self.lock = threading.Lock()

    def _remove(self, identifier):
        self.bytes -= len(self.entries.pop(identifier)[2].content)

    def _prune(self):
        now = self.clock()
        for identifier, (_, expires, _) in list(self.entries.items()):
            if expires <= now:
                self._remove(identifier)

    def publish_png(self, content, owner):
        if not isinstance(owner, str) or not owner or not isinstance(content, bytes) or not content.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('Expected a PNG and owner session')
        if len(content) > min(self.max_bytes, 8 * 1024 * 1024):
            raise ValueError('Artifact too large')
        with self.lock:
            self._prune()
            while self.entries and (len(self.entries) >= self.max_entries or self.bytes + len(content) > self.max_bytes):
                self._remove(next(iter(self.entries)))
            identifier = secrets.token_urlsafe(32)
            self.entries[identifier] = (owner, self.clock() + self.ttl, Asset(content, 'image/png', 'analysis.png'))
            self.bytes += len(content)
        return '/artifacts/' + identifier

    def read(self, identifier, owner):
        with self.lock:
            self._prune()
            item = self.entries.get(identifier)
            if not owner or item is None or not hmac.compare_digest(item[0].encode(), owner.encode()):
                return None
            return item[2]


def create_source_router(catalog, artifacts):
    from fastapi import APIRouter, Cookie, HTTPException
    from fastapi.responses import RedirectResponse, Response
    router = APIRouter()

    def asset_response(asset):
        if asset is None:
            raise HTTPException(status_code=404, detail='Source unavailable')
        return Response(asset.content, media_type=asset.media_type,
                        headers={'Cache-Control':'private, no-store', 'X-Content-Type-Options':'nosniff',
                                 'Content-Disposition':"inline; filename*=UTF-8''" + quote(asset.filename, safe='')})

    @router.get('/sources/{identifier}')
    def public_source(identifier: str):
        return asset_response(catalog.read(identifier))

    @router.get('/data/{path:path}', include_in_schema=False)
    def legacy_source(path: str):
        identifier = catalog.legacy_id('data/' + path)
        if identifier is None:
            raise HTTPException(status_code=404, detail='Source unavailable')
        return RedirectResponse('/sources/' + identifier, status_code=307)

    @router.get('/artifacts/{identifier}')
    def private_artifact(identifier: str, session_id: str = Cookie(None)):
        return asset_response(artifacts.read(identifier, session_id))

    return router
