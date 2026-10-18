"""Main-chat research workflow with real LangGraph, providers forbidden, and restart."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

from scripts.run_offline_ci import ROOT, test_environment


def verify(output, server_python, executable):
    import httpx
    from playwright.sync_api import sync_playwright
    if output.exists():
        raise FileExistsError('Use a fresh browser output directory')
    output.mkdir(parents=True)
    report = {'status': 'running', 'checks': [], 'page_errors': [], 'blocked_external_requests': [],
              'human_signoff': False, 'clinical_correctness_evaluated': False,
              'model_invocation_policy': 'all routing, conversation and guardrail calls raise',
              'llm_calls': 0, 'actual_server_restarts': 0}
    def passed(name):
        report['checks'].append(name)
    with tempfile.TemporaryDirectory(prefix='tcm-agent-research-') as temp, socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        env = test_environment()
        env.update(ENABLE_PHARMACOPOEIA_RESEARCH='1', CHAT_MEMORY_DB=str(Path(temp) / 'memory.sqlite3'),
                   PYTHONPYCACHEPREFIX=str(Path(temp) / 'pycache'))
        server, log = None, None
        def start():
            nonlocal server, log
            log = (output / ('restart.log' if report['actual_server_restarts'] else 'server.log')).open('w+')
            server = subprocess.Popen([str(server_python), '-m', 'scripts.verify_tcm_chat_ui', '--serve', str(sock.fileno())],
                cwd=ROOT, env=env, pass_fds=(sock.fileno(),), stdout=log, stderr=log)
            with httpx.Client(trust_env=False, timeout=.5) as client:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    try:
                        if client.get(url + '/health').status_code == 200:
                            return
                    except httpx.HTTPError:
                        pass
                    if server.poll() is not None:
                        break
                    time.sleep(.1)
            raise RuntimeError('Main-chat test server did not become ready; see local server log')
        def stop():
            if server and server.poll() is None:
                server.terminate(); server.wait(timeout=10)
            if log:
                log.close()
        try:
            start()
            with sync_playwright() as pw:
                options = {'headless': True}
                if executable:
                    options['executable_path'] = str(executable)
                browser = pw.chromium.launch(**options)
                ctx = browser.new_context(viewport={'width': 1280, 'height': 900})
                def page_for(context):
                    page = context.new_page()
                    page.on('pageerror', lambda e: report['page_errors'].append(str(e)))
                    def guard(route):
                        if route.request.url.startswith(url + '/') or route.request.url.startswith('blob:' + url + '/'):
                            route.continue_()
                        else:
                            report['blocked_external_requests'].append(route.request.url); route.abort()
                    page.route('**/*', guard); page.goto(url)
                    page.wait_for_function('()=>!document.getElementById("message-input").disabled')
                    return page
                page = page_for(ctx)
                def submit(question, expected):
                    previous = page.locator('[data-research-status]').count()
                    page.locator('#message-input').fill(question)
                    with page.expect_response(lambda r: r.url == url + '/chat') as response:
                        page.locator('#chat-form').evaluate('(f)=>f.requestSubmit()')
                    result = response.value.json()
                    assert result['agent'] == 'PHARMACOPOEIA_RESEARCH_AGENT', result
                    assert result['research']['status'] == expected, result['research']['status']
                    page.wait_for_function('(n)=>document.querySelectorAll("[data-research-status]").length===n', arg=previous + 1)
                    page.wait_for_function('()=>!document.getElementById("message-input").disabled')
                    return page.locator('[data-research-status]').last, result['research']

                card, result = submit('/research 甘草的功效', 'candidates')
                assert '甘草 · 功能与主治' in card.inner_text()
                assert result['workflow'] == 'langgraph_explicit_research_tool'
                card, _ = submit('/research 它的用量', 'candidates')
                assert '用法与用量' in card.inner_text()
                card, _ = submit('/research 出处呢', 'candidates')
                card.locator('summary').first.click(); card.get_by_role('button', name='查看原页').first.click()
                image = card.locator('img').first
                page.wait_for_function('(im)=>im.complete&&im.naturalWidth>0', arg=image.element_handle())
                passed('main_graph_explicit_tool_followup_and_original_pdf')
                with page.expect_download() as download:
                    card.locator('[data-evidence-export]').click()
                download.value.save_as(str(output / 'followup.md'))
                exported = (output / 'followup.md').read_text()
                assert '甘草的用量' in exported and '仅记录本轮查询字段' in exported
                assert '索引 SHA256' in exported and '全部未人工审核' in exported
                passed('main_followup_markdown_download_preserves_resolved_query_and_source')
                page.screenshot(path=str(output / 'desktop.png'), full_page=True)

                other = browser.new_context(); other_page = page_for(other)
                assert other_page.locator('[data-research-status]').count() == 0
                other.close(); passed('main_session_isolation')

                card, _ = submit('/research 酒大黄的用量', 'variant_scope_unresolved')
                with page.expect_download() as download:
                    card.locator('[data-evidence-export]').click()
                download.value.save_as(str(output / 'unresolved_scope.md'))
                assert '没有可用于该炮制品的字段结果' in (output / 'unresolved_scope.md').read_text()
                submit('/research 它的用量', 'clarify_entity')
                card, _ = submit('/research 炙甘草的用量', 'reference_unresolved')
                assert '同甘草' in card.inner_text()
                with page.expect_download() as download:
                    card.locator('[data-evidence-export]').click()
                download.value.save_as(str(output / 'reference.md'))
                assert '同甘草' in (output / 'reference.md').read_text()
                passed('scope_and_reference_exports_preserve_incomplete_status')
                card, result = submit('/research 黃芪的服用剂量', 'candidates')
                assert result['focus_name'] == '黄芪'
                assert '药名字形：黃芪 → 黄芪' in card.inner_text()
                assert '字段：剂量 → 用法与用量' in card.inner_text()
                with page.expect_download() as download:
                    card.locator('[data-evidence-export]').click()
                download.value.save_as(str(output / 'query_interpretation.md'))
                text = (output / 'query_interpretation.md').read_text()
                assert '黃芪 → 黄芪' in text and '剂量 → 用法与用量' in text
                card, result = submit('/research 它每次多少克', 'candidates')
                assert result['resolved_question'] == '黄芪每次多少克'
                passed('glyph_and_field_paraphrase_trace_survives_followup_and_export')
                submit('/research 酒黃芪的服用剂量', 'variant_scope_unresolved')
                submit('/research Huangqi的功效', 'name_not_resolved')
                passed('new_query_forms_keep_processed_scope_and_pinyin_abstention')
                submit('/research', 'invalid_query')
                submit('/research-clear', 'context_cleared')
                passed('scope_reference_and_command_errors_never_call_model')

                submit('/research 甘草的功效', 'candidates')
                state = ctx.storage_state(); before = page.request.get(url + '/conversation').json()
                browser.close(); stop(); report['actual_server_restarts'] = 1; start()
                browser = pw.chromium.launch(**options)
                ctx = browser.new_context(storage_state=state, viewport={'width': 1280, 'height': 900})
                page = page_for(ctx)
                restored = page.request.get(url + '/conversation').json()
                assert restored['messages'] == before['messages']
                assert restored['research']['focus_name'] == '甘草'
                submit('/research 它的用量', 'candidates')
                passed('actual_main_process_and_browser_restart_restore_research_focus')

                assert page.request.get(url + '/api/research/page?chunk=../../data&kind=evidence').status == 404
                assert page.request.get(url + '/api/research/page?chunk=x&index=-1').status == 400
                assert page.request.get(url + '/api/research/page?chunk=x&path=secret').status == 400
                page.request.delete(url + '/conversation'); page.reload()
                page.wait_for_function('()=>!document.getElementById("message-input").disabled')
                submit('/research 它的用量', 'clarify_entity')
                passed('delete_and_invalid_evidence_requests')

                submit('/research <img src=x onerror="window.bad=1">甘草的功效', 'candidates')
                assert not page.evaluate('Boolean(window.bad)')
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth')
                page.screenshot(path=str(output / 'mobile.png'), full_page=True)
                passed('source_and_user_text_stay_literal_and_mobile_layout_fits')
                assert not report['page_errors']
                browser.close()
            report['status'] = 'passed'
        except Exception as error:
            report.update(status='failed', error=f'{type(error).__name__}: {error}')
            raise
        finally:
            stop()
            (output / 'browser.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'checks': len(report['checks'])}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'var/agent_research_ui_v1')
    parser.add_argument('--server-python', type=Path, default=Path('/tmp/tcm-ci-20261013/bin/python'))
    parser.add_argument('--executable', type=Path)
    args = parser.parse_args()
    verify(args.out, args.server_python, args.executable)
