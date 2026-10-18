"""Real browser and process restart verification with an isolated local research DB."""
import argparse
import json
import os
from pathlib import Path
import select
import subprocess
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def run(server_python, executable, output):
    from playwright.sync_api import sync_playwright
    if output.exists():
        raise FileExistsError('Use a fresh chat verification directory')
    output.mkdir(parents=True)
    report = {'status': 'running', 'checks': [], 'page_errors': [], 'external_requests': [],
              'llm_calls': 0, 'human_signoff': False, 'clinical_correctness_evaluated': False}
    process = None

    def passed(name):
        report['checks'].append(name)

    def start(port=0):
        nonlocal process
        process = subprocess.Popen([str(server_python), '-m', 'scripts.serve_mineru_research', '--port', str(port),
                                    '--memory', str(output / 'memory.sqlite3')], cwd=ROOT,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   env={**os.environ, 'PYTHONPYCACHEPREFIX': str(output / 'pycache')})
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if select.select([process.stdout], [], [], 1)[0]:
                line = process.stdout.readline().strip()
                if line.startswith('MinerU research: '):
                    return line.split(': ', 1)[1].rstrip('/')
            if process.poll() is not None:
                raise RuntimeError('Research test server exited before readiness')
        raise TimeoutError('Research server did not become ready')

    def stop():
        nonlocal process
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        if process and process.stdout:
            process.stdout.close()
        process = None

    try:
        url = start()
        with sync_playwright() as pw:
            options = {'headless': True}
            if executable:
                options['executable_path'] = str(executable)
            browser = pw.chromium.launch(**options)
            context = browser.new_context(viewport={'width': 1280, 'height': 900})

            def new_page(ctx):
                page = ctx.new_page()
                page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                def guard(route):
                    if route.request.url.startswith(url + '/') or route.request.url.startswith('blob:' + url + '/'):
                        route.continue_()
                    else:
                        report['external_requests'].append(route.request.url); route.abort()
                page.route('**/*', guard)
                page.goto(url + '/chat')
                page.wait_for_function('()=>!document.getElementById("send").disabled')
                return page

            page = new_page(context)
            def send(question):
                page.locator('#question').fill(question); page.locator('#send').click()
                page.wait_for_function('()=>!document.getElementById("send").disabled')
            assert page.locator('#history article').count() == 0
            send('它的用量')
            assert '没有已确认药材' in page.locator('#status').inner_text()
            passed('followup_without_entity_clarifies')

            send('甘草的功效')
            assert '当前药材：甘草' in page.locator('#focus').inner_text()
            assert '甘草 · 功能与主治' in page.locator('#evidence').inner_text()
            send('它的用量')
            assert '甘草 · 用法与用量' in page.locator('#evidence').inner_text()
            send('出处呢')
            card = page.locator('#evidence article').first
            card.locator('summary').first.click(); card.get_by_role('button', name='查看原页').first.click()
            image = card.locator('img').first
            page.wait_for_function('(im)=>im.complete&&im.naturalWidth>0', arg=image.element_handle())
            passed('named_question_followup_and_original_source_preview')
            with page.expect_download() as download:
                page.locator('[data-evidence-export]').click()
            download.value.save_as(str(output / 'followup.md'))
            assert '甘草的用量' in (output / 'followup.md').read_text()
            receipt = page.request.get(url + '/api/info').json()
            params = {'q': '甘草的用量', 'snapshot': receipt['index_sha256']}
            assert page.request.get(url + '/api/research/report', params=params).status == 200
            assert page.request.get(url + '/api/research/report', params={**params, 'snapshot': '0'*64}).status == 409
            assert page.request.get(url + '/api/research/report', params={**params, 'path': 'secret'}).status == 400
            passed('standalone_chat_download_and_export_snapshot_parameter_guards')
            page.goto(url)
            page.wait_for_function('()=>!document.getElementById("search").disabled')
            page.locator('#q').fill('麝香的用量'); page.locator('#search').click()
            page.wait_for_function('()=>!document.getElementById("search").disabled')
            with page.expect_download() as download:
                page.locator('[data-evidence-export]').click()
            download.value.save_as(str(output / 'research_table.md'))
            exported = (output / 'research_table.md').read_text()
            assert '多人丸散用' in exported and '多入丸散用' in exported
            passed('research_table_download_keeps_original_and_revised_text')
            page.goto(url + '/chat')
            page.wait_for_function('()=>!document.getElementById("send").disabled')
            page.screenshot(path=str(output / 'desktop.png'), full_page=True)

            send('黃芪')
            assert '当前药材：黄芪' in page.locator('#focus').inner_text()
            send('它每次多少克')
            assert '黄芪 · 用法与用量' in page.locator('#evidence').inner_text()
            assert '字段：多少克 → 用法与用量' in page.locator('#evidence').inner_text()
            send('黨參的药效')
            assert '药名字形：黨參 → 党参' in page.locator('#evidence').inner_text()
            passed('name_only_glyph_selection_and_field_paraphrase_followup')

            send('酒大黄的用量')
            assert '炮制品范围未解析' in page.locator('#status').inner_text()
            assert '不能替代' in page.locator('#evidence').inner_text()
            send('它的用量')
            assert '没有已确认药材' in page.locator('#status').inner_text()
            send('炙甘草的用量')
            assert '引用尚未展开' in page.locator('#status').inner_text()
            assert '同甘草' in page.locator('#evidence').inner_text()
            passed('scope_and_reference_failures_cannot_reuse_previous_focus')

            other = browser.new_context()
            second = new_page(other)
            assert second.locator('#history article').count() == 0
            other.close()
            passed('separate_browser_session_isolation')

            state = page.request.get(url + '/api/research/conversation').json()
            for payload in [{'question': '甘草的功效', 'revision': state['revision'], 'history': []},
                            {'question': '', 'revision': state['revision']},
                            {'question': '问题', 'revision': True}]:
                assert page.request.post(url + '/api/research/chat', data=payload).status == 400
            assert page.request.post(url + '/api/research/chat', data={'question': '甘草的功效', 'revision': 0}).status == 409
            assert page.request.post(url + '/api/research/chat', data={'question': '甘草的功效', 'revision': state['revision']},
                                     headers={'Origin': 'http://untrusted.invalid'}).status == 403
            assert page.request.get(url + '/api/research/conversation').json()['revision'] == state['revision']
            anonymous = pw.request.new_context()
            assert anonymous.post(url + '/api/research/chat', data={'question': '甘草的功效', 'revision': 0}).status == 410
            anonymous.dispose()
            passed('client_history_invalid_revision_and_foreign_origin_rejected')

            send('甘草的功效')
            before = page.request.get(url + '/api/research/conversation').json()
            storage = context.storage_state()  # In-memory only; do not write session credentials to reports.
            port = urlsplit(url).port
            browser.close(); stop(); url = start(port)
            browser = pw.chromium.launch(**options)
            context = browser.new_context(storage_state=storage, viewport={'width': 1280, 'height': 900})
            page = new_page(context)
            after = page.request.get(url + '/api/research/conversation').json()
            assert before == after and '当前药材：甘草' in page.locator('#focus').inner_text()
            send('它的用量')
            assert '甘草 · 用法与用量' in page.locator('#evidence').inner_text()
            report['actual_server_restarts'] = 1; report['actual_browser_restarts'] = 1
            passed('server_and_browser_restart_restore_signed_session_and_followup')

            old_cookie = next(c['value'] for c in context.cookies() if c['name'] == 'research_session')
            old_revision = page.request.get(url + '/api/research/conversation').json()['revision']
            page.locator('#clear').click()
            page.wait_for_function('()=>!document.getElementById("send").disabled')
            assert page.locator('#history article').count() == 0
            isolated = pw.request.new_context(extra_http_headers={'Cookie': 'research_session=' + old_cookie})
            assert isolated.post(url + '/api/research/chat', data={'question': '甘草的功效', 'revision': old_revision}).status == 410
            isolated.dispose()
            send('它的用量')
            assert '没有已确认药材' in page.locator('#status').inner_text()
            passed('deletion_blocks_old_cookie_and_clears_focus')

            send('<script>window.bad=1</script>甘草的功效')
            assert page.evaluate('window.bad') is None
            assert '<script>' in page.locator('#history .user').last.inner_text()
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(output / 'mobile.png'), full_page=True)
            passed('user_text_is_not_executed_and_mobile_layout_fits')
            assert not report['page_errors'] and not report['external_requests']
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
    parser.add_argument('--server-python', type=Path, default=Path('/tmp/tcm-ocr-sample-20261016/bin/python'))
    parser.add_argument('--executable', type=Path)
    parser.add_argument('--out', type=Path, default=ROOT / 'var/mineru_chat_verification_v1')
    args = parser.parse_args()
    run(args.server_python, args.executable, args.out)
