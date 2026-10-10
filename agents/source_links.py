"""Keep local document links valid behind any host/port; preserve external URLs."""
from urllib.parse import quote, unquote, urlsplit
from posixpath import normpath


def source_url(value):
    if not value:
        return value
    parsed = urlsplit(str(value))
    if parsed.scheme or parsed.netloc:
        if parsed.scheme not in ('http', 'https') or parsed.hostname not in ('localhost', '127.0.0.1'):
            return str(value)
        path = parsed.path
    else:
        path = str(value).replace('\\', '/')
    path = normpath(path)
    if path.startswith('data/'):
        path = '/' + path
    if not path.startswith('/data/'):
        return str(value)
    return quote(unquote(path), safe='/')
