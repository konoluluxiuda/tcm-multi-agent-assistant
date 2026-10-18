"""Local, read-only MinerU research service with source-bound page previews."""
from http.server import BaseHTTPRequestHandler
from http.cookies import SimpleCookie, CookieError
import json
import math
from pathlib import Path
import sqlite3
import threading
from urllib.parse import parse_qs, urlsplit

from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_retrieval import ResearchRetriever
from agents.conversation_store import MemoryUnavailable, ConversationConflict, SessionExpired


class SnapshotChanged(RuntimeError):
    pass


def signature(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


class ResearchService:
    def __init__(self, directory, sources):
        self.directory = Path(directory).resolve()
        self.sources = {name: Path(path).resolve() for name, path in sources.items()}
        paths = [self.directory / 'manifest.json', self.directory / 'research.sqlite3', *self.sources.values()]
        self.frozen = {path: signature(path) for path in paths}
        retriever = ResearchRetriever(self.directory)
        try:
            self.receipt = retriever.receipt
            if self.receipt['human_signoff'] is not False or self.receipt['research_only'] is not True:
                raise ValueError('Expected an unreviewed research index')
            self.source_hashes = {name: digest(path) for name, path in self.sources.items()}
            # Check every citation, including title anchors, before enabling the page route.
            for row in retriever.connection.execute('SELECT payload FROM chunks'):
                payload = json.loads(row[0])
                for citation in payload['evidence'] + payload['title_evidence']:
                    self.validate_citation(citation)
        finally:
            retriever.close()
        self.render_lock = threading.Lock()
        self.check()

    def check(self):
        try:
            changed = any(signature(path) != stamp for path, stamp in self.frozen.items())
        except OSError:
            changed = True
        if changed:
            raise SnapshotChanged('索引或来源文件已改变，请校验并重启研究服务。')

    def validate_citation(self, citation):
        source = citation['source']
        alias = source['source_document']
        if alias not in self.sources or source['source_sha256'] != self.source_hashes[alias]:
            raise ValueError('Citation source does not match configured PDF')
        page = source['source_pdf_page']
        box = citation['bbox_0_1000']
        if type(page) is not int or page < 1:
            raise ValueError('Invalid physical PDF page')
        if (len(box) != 4 or any(type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 1000 for x in box)
                or box[0] >= box[2] or box[1] >= box[3]):
            raise ValueError('Invalid citation coordinates')
        return alias, page, box

    def search(self, question, method='name_field', k=5):
        self.check()
        # Each HTTP worker owns a separate SQLite connection, always opened read-only.
        retriever = ResearchRetriever(self.directory)
        try:
            result = retriever.query(question, method=method, k=k)
        finally:
            retriever.close()
        self.check()
        return result

    def preview(self, chunk, kind, index):
        self.check()
        if kind not in ('evidence', 'title_evidence') or type(index) is not int or index < 0:
            raise ValueError('Invalid evidence selector')
        retriever = ResearchRetriever(self.directory)
        try:
            row = retriever.connection.execute('SELECT payload FROM chunks WHERE chunk_id=?', (chunk,)).fetchone()
            if not row:
                raise LookupError('Evidence not found')
            citations = json.loads(row[0])[kind]
            if index >= len(citations):
                raise LookupError('Evidence not found')
            citation = citations[index]
        finally:
            retriever.close()
        alias, page, box = self.validate_citation(citation)
        import pymupdf
        # Serialize rendering to bound memory use. No client-controlled file path or scale.
        with self.render_lock, pymupdf.open(self.sources[alias]) as document:
            if page > len(document):
                raise ValueError('Citation page exceeds source PDF')
            original = document[page - 1]
            pixmap = original.get_pixmap(matrix=pymupdf.Matrix(1.3, 1.3), alpha=False)
            from PIL import Image, ImageDraw
            image = Image.frombytes('RGB', (pixmap.width, pixmap.height), pixmap.samples)
            rectangle = [box[0] * image.width / 1000, box[1] * image.height / 1000,
                         box[2] * image.width / 1000, box[3] * image.height / 1000]
            ImageDraw.Draw(image).rectangle(rectangle, outline='#e11d48', width=3)
            import io
            stream = io.BytesIO()
            image.save(stream, format='PNG')
            result = stream.getvalue()
        self.check()
        return result


def make_handler(service, chat=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_bytes(self, status, body, kind):
            self.send_response(status)
            for name, value in [('Content-Type', kind), ('Content-Length', str(len(body))),
                                ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'),
                                ('Content-Security-Policy', "default-src 'self'; img-src 'self' blob:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")]:
                self.send_header(name, value)
            if getattr(self, 'session_value', None):
                self.send_header('Set-Cookie', f'research_session={self.session_value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={chat.store.ttl_seconds}')
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, status, value):
            self.send_bytes(status, json.dumps(value, ensure_ascii=False).encode(), 'application/json; charset=utf-8')

        def chat_cookie(self, create=False):
            cookies = SimpleCookie()
            try:
                cookies.load(self.headers.get('Cookie', ''))
            except CookieError:
                pass
            value = cookies['research_session'].value if 'research_session' in cookies else None
            if create:
                value = chat.session(value)
            elif not chat.store.valid_cookie(value):
                raise SessionExpired('Start a research conversation first')
            self.session_value = value
            return value

        def chat_origin(self):
            allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            host = self.headers.get('Host')
            if host not in allowed or self.headers.get('Origin') not in (None, 'http://' + host):
                raise PermissionError('Invalid origin')

        def do_POST(self):
            self.chat_mutation('turn')

        def do_DELETE(self):
            self.chat_mutation('delete')

        def chat_mutation(self, action):
            expected = '/api/research/chat' if action == 'turn' else '/api/research/conversation'
            if chat is None or self.path != expected:
                self.send_json(404, {'error': 'Unknown endpoint'})
                return
            try:
                self.chat_origin()
                if action == 'delete':
                    cookie = self.chat_cookie(create=True)
                    chat.delete(cookie)
                    self.session_value = chat.store.new_cookie()
                    self.send_json(200, {'status': 'cleared', 'revision': 0})
                    return
                if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                    raise ValueError('JSON required')
                length = int(self.headers.get('Content-Length', '0'))
                if not 1 <= length <= 8192 or self.headers.get('Transfer-Encoding'):
                    raise ValueError('Invalid body length')
                def unique(pairs):
                    if len({key for key, _ in pairs}) != len(pairs):
                        raise ValueError('Duplicate JSON fields')
                    return dict(pairs)
                body = json.loads(self.rfile.read(length), object_pairs_hook=unique)
                if not isinstance(body, dict) or set(body) != {'question', 'revision'}:
                    raise ValueError('Invalid body')
                result = chat.turn(self.chat_cookie(), body['question'], body['revision'])
                self.send_json(200, result)
            except PermissionError:
                self.send_json(403, {'error': '请求来源不匹配。'})
            except (ValueError, UnicodeError):
                self.send_json(400, {'error': '请输入1–500字符的问题与有效会话版本。'})
            except ConversationConflict:
                self.send_json(409, {'error': '会话已改变，请重新加载后再提交。'})
            except SessionExpired:
                self.send_json(410, {'error': '会话已删除或过期，请重新加载。'})
            except SnapshotChanged as error:
                self.send_json(409, {'error': str(error)})
            except MemoryUnavailable:
                self.send_json(503, {'error': '持久会话存储不可用，未切换到临时记忆。'})
            except (OSError, sqlite3.Error):
                self.send_json(503, {'error': '研究服务暂不可用，本轮未保存。'})

        def do_GET(self):
            uri = urlsplit(self.path)
            try:
                service.check()
                if uri.path == '/':
                    self.send_bytes(200, Path(__file__).with_name('mineru_research.html').read_bytes(), 'text/html; charset=utf-8')
                    return
                if uri.path == '/research-evidence.js':
                    self.send_bytes(200, Path(__file__).with_name('mineru_evidence.js').read_bytes(), 'text/javascript; charset=utf-8')
                    return
                if uri.path == '/chat' and chat is not None:
                    self.chat_origin()
                    self.send_bytes(200, Path(__file__).with_name('mineru_chat.html').read_bytes(), 'text/html; charset=utf-8')
                    return
                if uri.path == '/api/research/conversation' and chat is not None:
                    self.chat_origin()
                    if uri.query:
                        raise ValueError('No query parameters allowed')
                    self.send_json(200, chat.conversation(self.chat_cookie(create=True)))
                    return
                params = parse_qs(uri.query, keep_blank_values=True, max_num_fields=4)
                allowed = {'/api/info': set(), '/api/search': {'q', 'method', 'k'},
                           '/api/page': {'chunk', 'kind', 'index'}}
                if uri.path not in allowed:
                    self.send_json(404, {'error': 'Unknown endpoint'})
                    return
                if set(params) - allowed[uri.path] or any(len(v) != 1 for v in params.values()):
                    raise ValueError('Unknown or duplicate query parameter')
                values = {k: v[0] for k, v in params.items()}
                if uri.path == '/api/info':
                    self.send_json(200, service.receipt)
                elif uri.path == '/api/search':
                    self.send_json(200, service.search(values.get('q', ''), values.get('method', 'name_field'), int(values.get('k', '5'))))
                else:
                    self.send_bytes(200, service.preview(values.get('chunk', ''), values.get('kind', 'evidence'), int(values.get('index', '0'))), 'image/png')
            except SnapshotChanged as error:
                self.send_json(409, {'error': str(error)})
            except PermissionError:
                self.send_json(403, {'error': '请求来源不匹配。'})
            except MemoryUnavailable:
                self.send_json(503, {'error': '持久会话存储不可用，未切换到临时记忆。'})
            except SessionExpired:
                self.send_json(410, {'error': '会话已删除或过期，请重新加载。'})
            except (ValueError, KeyError):
                self.send_json(400, {'error': '参数无效或来源定位不匹配。'})
            except LookupError:
                self.send_json(404, {'error': '未找到该证据。'})
            except (OSError, ImportError):
                self.send_json(503, {'error': '来源预览不可用，请检查文件与 PDF 处理依赖。'})
            except sqlite3.Error:
                self.send_json(503, {'error': '研究索引不可用，请检查索引快照。'})
    return Handler
