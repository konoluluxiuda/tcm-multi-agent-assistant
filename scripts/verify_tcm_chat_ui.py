"""Check the real main-chat UI with model invocations forbidden and CDN requests blocked.

Run with a Playwright Python environment; --server-python must contain CI dependencies.
Without --database this creates synthetic source records for HTML trust-boundary checks.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

from scripts.run_offline_ci import ROOT, test_environment


def serve(fd):
    import dotenv
    dotenv.load_dotenv = lambda *a, **kw: False
    import agents.agent_decision as ad
    from langchain_core.runnables import RunnableLambda
    import uvicorn
    def forbidden(_):
        raise AssertionError('Model invocation forbidden in chat UI verification')
    ad.config.agent_decision.llm = RunnableLambda(forbidden)
    ad.config.conversation.llm = RunnableLambda(forbidden)
    ad.LocalGuardrails.check_input = forbidden
    ad.LocalGuardrails.check_output = forbidden
    uvicorn.run('app:app', fd=fd, log_level='error')


def verify(database, output, server_python, executable):
    import httpx
    from playwright.sync_api import sync_playwright
    from tcm.herbs import import_snapshot
    with tempfile.TemporaryDirectory(prefix='tcm-chat-ui-') as temporary:
        temporary = Path(temporary)
        synthetic = database is None
        if synthetic:
            source = temporary / 'source/etcm_herbs'
            source.mkdir(parents=True)
            for filename, name in [('BaiBu', 'BaiBu'), ('one', 'HuaShi'), ('two', 'HuaShi')]:
                obj = {'code': 1, 'data': [{'id': 'base_information', 'value': [
                    {'key': 'Herb Name in Pinyin', 'value': name},
                    {'key': 'Indications', 'value': '<img src=x onerror="window.sourceExecuted=true"> source text'},
                    {'key': 'PubMed', 'value': ['123']}]}]}
                (source / (filename + '.json')).write_text(json.dumps(obj), encoding='utf-8')
            database = temporary / 'fixture.sqlite3'
            import_snapshot(source.parent, database, 'browser-fixture-v1')
        initial = hashlib.sha256(database.read_bytes()).hexdigest()
        report = {'status': 'running', 'synthetic': synthetic, 'database_sha256': initial,
                  'human_signoff': False, 'task_completion_rate': None,
                  'cases': [], 'page_errors': [], 'blocked_external_requests': [],
                  'source_code_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                    for name in ['tcm/chat.py', 'agents/agent_decision.py', 'app.py', 'templates/index.html',
                                 'scripts/verify_tcm_chat_ui.py']}}
        env = test_environment()
        env['TCM_DB'] = str(database.resolve())
        env['TCM_DATASET_VERSION'] = ''
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
            with (temporary / 'server.log').open('w+') as log:
                server = subprocess.Popen([str(server_python), '-m', 'scripts.verify_tcm_chat_ui', '--serve',
                                           str(sock.fileno())], cwd=ROOT, env=env,
                                          pass_fds=(sock.fileno(),), stdout=log, stderr=log)
                try:
                    url = f'http://127.0.0.1:{port}'
                    with httpx.Client(trust_env=False, timeout=.5) as client:
                        deadline = time.monotonic() + 20
                        while True:
                            try:
                                if client.get(url + '/health').status_code == 200: break
                            except httpx.HTTPError: pass
                            if time.monotonic() > deadline or server.poll() is not None:
                                log.seek(0)
                                raise RuntimeError('Server startup failed: ' + log.read())
                            time.sleep(.1)
                    with sync_playwright() as pw:
                        options = {'headless': True}
                        if executable: options['executable_path'] = str(executable)
                        browser = pw.chromium.launch(**options)
                        report['browser_version'] = browser.version
                        page = browser.new_page(viewport={'width': 1280, 'height': 900})
                        page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                        def guard(route):
                            if route.request.url.startswith(url + '/'):
                                route.continue_()
                            else:
                                report['blocked_external_requests'].append(route.request.url)
                                route.abort()
                        page.route('**/*', guard)
                        page.goto(url)
                        def submit(query, status):
                            previous = page.locator('[data-tcm-status]').count()
                            page.locator('#message-input').fill(query)
                            with page.expect_response(lambda r: r.url == url + '/chat') as response:
                                page.locator('#chat-form').evaluate('(form) => form.requestSubmit()')
                            body = response.value.json()
                            assert body['tcm']['status'] == status, body
                            page.wait_for_function('(n) => document.querySelectorAll("[data-tcm-status]").length === n', arg=previous+1)
                            last = page.locator('[data-tcm-status]').last
                            assert last.get_attribute('data-tcm-status') == status
                            return last, body
                        last, body = submit('/herb BaiBu', 'ok')
                        assert last.locator('.herb-source-card').count() == 1
                        assert '字段出处与原值' in last.inner_text()
                        assert body['tcm']['records'][0]['source_record_id'] in last.inner_text()
                        report['cases'].append({'case': 'name_lookup_provenance', 'status': 'passed'})
                        last, body = submit('/herb HuaShi', 'ambiguous_entity')
                        assert last.locator('.herb-source-card').count() == 2
                        chosen = body['tcm']['records'][1]['source_record_id']
                        with page.expect_response(lambda r: r.url == url + '/chat') as response:
                            last.get_by_role('button', name='选择此来源').nth(1).click()
                        assert response.value.json()['tcm']['records'][0]['source_record_id'] == chosen
                        page.wait_for_function('() => document.querySelectorAll("[data-tcm-status]").length === 3')
                        assert page.locator('[data-tcm-status]').last.get_attribute('data-tcm-status') == 'ok'
                        report['cases'].append({'case': 'ambiguity_then_source_choice', 'status': 'passed'})
                        for query, status in [('/herb 百部', 'unsupported_name_mapping'),
                                              ('/herb nonexistent-fixture-name', 'not_found'),
                                              ('/herb', 'invalid_query')]:
                            submit(query, status)
                            report['cases'].append({'case': status, 'status': 'passed'})
                        if synthetic:
                            assert '<img src=x onerror=' in page.locator('[data-tcm-status]').first.inner_text()
                            submit('/herb <img src=x onerror="window.userExecuted=true">', 'not_found')
                            assert not page.evaluate('Boolean(window.sourceExecuted || window.userExecuted)')
                            assert page.locator('.herb-source-card img, .user-message img').count() == 0
                            report['cases'].append({'case': 'source_and_user_html_literal', 'status': 'passed'})
                        assert not report['page_errors'], report['page_errors']
                        browser.close()
                    assert hashlib.sha256(database.read_bytes()).hexdigest() == initial
                    report['status'] = 'passed'
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                    print(json.dumps({'status': 'passed', 'cases': len(report['cases']), 'synthetic': synthetic}))
                finally:
                    server.terminate()
                    try: server.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', type=int)
    parser.add_argument('--database', type=Path)
    parser.add_argument('--out', type=Path, default=Path('/tmp/tcm-chat-ui.json'))
    parser.add_argument('--server-python', type=Path)
    parser.add_argument('--executable', type=Path)
    args = parser.parse_args()
    if args.serve is not None:
        serve(args.serve)
    else:
        if args.server_python is None: parser.error('--server-python is required')
        verify(args.database, args.out, args.server_python, args.executable)
