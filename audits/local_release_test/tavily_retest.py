"""Run against the isolated release container; writes incremental JSON evidence."""
import json, time, urllib.request, urllib.error
from pathlib import Path
BASE = 'http://127.0.0.1:7861'
OUT = Path(__file__).with_name('tavily_retest_results.json')
results = []

def probe(name, path, payload=None, expected=200, raw=None, content_type=None, cookie=None):
    data = json.dumps(payload).encode() if payload is not None else raw
    headers = {}
    if data is not None:
        headers['Content-Type'] = content_type or 'application/json'
    if cookie:
        headers['Cookie'] = cookie
    req = urllib.request.Request(BASE + path, data=data, headers=headers)
    t = time.monotonic()
    try:
        try:
            response = urllib.request.urlopen(req, timeout=150)
        except urllib.error.HTTPError as e:
            response = e
        body = response.read().decode('utf-8', errors='replace')
        try:
            body = json.loads(body)
        except ValueError:
            body = body[:180]
        rec = {'name': name, 'path': path, 'http_status': response.code, 'expected_status': expected, 'http_pass': response.code == expected, 'body': body}
    except Exception as e:
        rec = {'name': name, 'path': path, 'http_pass': False, 'error': str(e)}
    rec['elapsed_s'] = round(time.monotonic() - t, 2)
    results.append(rec)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(json.dumps(rec, ensure_ascii=False), flush=True)

def upload(name, filename, content, expected):
    boundary = 'release-smoke-boundary'
    raw = f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode() + content + f'\r\n--{boundary}--\r\n'.encode()
    probe(name, '/upload', expected=expected, raw=raw, content_type='multipart/form-data; boundary=' + boundary)
probe("health_after_env_reload", "/health")
probe("medical_rag_with_tavily", "/chat", {"query": "What general symptoms of brain tumors are described in the medical literature?"}, cookie="session_id=tavily-retest")
probe("request_sources_with_tavily", "/chat", {"query": "请提供刚才回答的参考文献来源和页码。"}, cookie="session_id=tavily-retest")
