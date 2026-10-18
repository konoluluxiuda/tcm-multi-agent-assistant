"""Serve the local research batch with read-only APIs, bound to loopback only."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qs, unquote, urlsplit

from tcm.pharmacopoeia import digest
from tcm.pharmacopoeia_research import search_index

ROOT = Path(__file__).resolve().parents[1]


def make_handler(directory, policy_path):
    directory = directory.resolve()
    batch = json.loads((directory/'quality.json').read_text())
    fingerprint = digest(directory/'quality.json')
    probe = search_index(directory/'research.sqlite3', field='pinyin', limit=1)
    if probe['batch_sha256'] != fingerprint:
        raise ValueError('Index and quality batch do not match')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def send_bytes(self, status, body, content_type):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers(); self.wfile.write(body)

        def send_json(self, status, value):
            self.send_bytes(status, json.dumps(value, ensure_ascii=False).encode(), 'application/json; charset=utf-8')

        def do_GET(self):
            uri = urlsplit(self.path); path = unquote(uri.path)
            if path.startswith('/api/'):
                if digest(policy_path) != batch['policy_sha256']:
                    self.send_json(409, {'error': '质检策略已改变，请重建批次再检索。'}); return
                try:
                    if path == '/api/quality':
                        self.send_json(200, batch); return
                    if path != '/api/search': self.send_json(404, {'error': 'Unknown endpoint'}); return
                    params = parse_qs(uri.query, keep_blank_values=True, max_num_fields=5)
                    if set(params)-{'q','herb','field','limit'} or any(len(v) != 1 for v in params.values()):
                        raise ValueError('Unknown or duplicate query parameter')
                    result = search_index(directory/'research.sqlite3', params.get('q',[''])[0],
                        params.get('herb',[''])[0], params.get('field',[''])[0], int(params.get('limit',['10'])[0]))
                    self.send_json(200, result)
                except ValueError as error: self.send_json(400, {'error': str(error)})
                except sqlite3.Error: self.send_json(503, {'error': '研究索引不可用。'})
                return
            if path == '/': file, kind = directory/'index.html', 'text/html; charset=utf-8'
            elif re.fullmatch(r'/assets/[A-Za-z0-9_-]+\.png', path): file, kind = directory/path[1:], 'image/png'
            else: self.send_error(404); return
            if not file.is_file() or not file.resolve().is_relative_to(directory): self.send_error(404); return
            self.send_bytes(200, file.read_bytes(), kind)
    return Handler


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT/'var/pharmacopoeia_research')
    parser.add_argument('--policy', type=Path, default=ROOT/'eval/pharmacopoeia_qc_policy_v1.json')
    parser.add_argument('--port', type=int, default=7868)
    args = parser.parse_args()
    with ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(args.directory, args.policy)) as server:
        print(f'Research preview: http://127.0.0.1:{server.server_port}/', flush=True)
        server.serve_forever()
