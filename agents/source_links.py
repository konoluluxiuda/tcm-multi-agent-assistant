"""Canonical opaque URLs for local source files; safe external HTTP URLs remain."""
import hashlib
import re
from posixpath import normpath
from urllib.parse import unquote, urlsplit

SOURCE_FOLDERS = {'raw', 'raw_extras', 'parsed_docs'}


def local_source_path(value):
    """Resolve legacy local links lexically; never accept parent traversal."""
    if not value:
        return None
    raw = str(value).replace('\\', '/')
    parsed = urlsplit(raw)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme not in ('http', 'https') or parsed.hostname not in ('localhost', '127.0.0.1'):
            return None
        raw = parsed.path
    else:
        raw = parsed.path
    path = unquote(raw)
    if '\\' in path or '\x00' in path or re.search(r'%[0-9a-fA-F]{2}', path) or any(p == '..' for p in path.split('/')):
        return None
    path = normpath(path).lstrip('/')
    parts = path.split('/')
    if len(parts) < 3 or parts[0] != 'data' or parts[1] not in SOURCE_FOLDERS:
        return None
    return path


def source_id(path):
    normalized = local_source_path(path)
    if normalized is None:
        raise ValueError('Not an allowed source path')
    return 's_' + hashlib.sha256(normalized.encode()).hexdigest()


def source_url(value):
    if not value:
        return value
    parsed = urlsplit(str(value))
    if parsed.scheme in ('http', 'https') and parsed.hostname not in ('localhost', '127.0.0.1'):
        return str(value)
    if (not parsed.scheme and not parsed.netloc or parsed.scheme in ('http', 'https') and parsed.hostname in ('localhost','127.0.0.1')) and re.fullmatch(r'/sources/s_[0-9a-f]{64}', parsed.path):
        from urllib.parse import quote
        return parsed.path + ('#' + quote(parsed.fragment, safe='=') if parsed.fragment else '')
    path = local_source_path(value)
    if path is None:
        return None
    url = '/sources/' + source_id(path)
    if parsed.fragment:
        from urllib.parse import quote
        url += '#' + quote(parsed.fragment, safe='=')
    return url
