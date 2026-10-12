"""Real Chromium checks against a temporary local TCM API server.

Requires FastAPI, Uvicorn, HTTPX, Playwright and installed Chromium.
Run: python -m scripts.verify_tcm_page
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright


def main():
    results = []
    env = {**os.environ, 'TCM_DB': str(Path('var/tcm/herbs.sqlite3').resolve()),
           'TCM_DATASET_VERSION': 'local-2026-10-10'}
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        with subprocess.Popen([sys.executable, '-m', 'uvicorn', 'tcm.api:app', '--fd', str(sock.fileno())],
                              env=env, pass_fds=(sock.fileno(),), stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL) as server:
            try:
                url = f'http://127.0.0.1:{port}'
                with httpx.Client(trust_env=False, timeout=1) as client:
                    for attempt in range(100):
                        try:
                            if client.get(url + '/health').status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        if server.poll() is not None:
                            raise RuntimeError('Temporary API server exited')
                        time.sleep(.1)
                    else:
                        raise RuntimeError('Temporary API server did not start')
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True)
                    page = browser.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(url)
                    page.evaluate("window.name = 'existing-window-name'")

                    def click(label, locator=None):
                        with page.expect_response(lambda r: '/herb' in r.url) as received:
                            (locator or page.get_by_role('button', name=label, exact=True)).click()
                        response = received.value
                        page.wait_for_function("() => document.getElementById('status').textContent !== '查询中…'")
                        return response, response.json()

                    page.locator('#name').fill('HuangQin')
                    response, body = click('查详情')
                    assert response.url.endswith('/herbs/HuangQin') and body['status'] == 'ok'
                    original = body
                    assert page.locator('#detail h2').inner_text() == 'HuangQin'
                    results.append('detail button works with existing window.name; structured detail visible')
                    page.locator('#detail summary').first.click()
                    assert '/data/' in page.locator('#detail pre').first.inner_text()
                    assert 'SHA-256' in page.locator('#detail pre').first.inner_text()
                    results.append('field evidence expands with source path, pointer and hash')

                    page.locator('#conditions select').select_option('family')
                    page.locator('#conditions input').fill('Leguminosae')
                    _, first = click('筛选')
                    assert first['count'] == 127 and len(first['records']) == 20
                    assert page.locator('#results table tr').count() == 21
                    assert page.get_by_role('button', name='上一页', exact=True).is_disabled()
                    results.append('filter shows first 20 of 127 records in table')
                    response, second = click('下一页')
                    assert second['offset'] == 20 and 'dataset_version=local-2026-10-10' in response.url
                    assert not ({r['source_record_id'] for r in first['records']} & {r['source_record_id'] for r in second['records']})
                    _, back = click('上一页')
                    assert [r['source_record_id'] for r in first['records']] == [r['source_record_id'] for r in back['records']]
                    results.append('next/previous pages stable and pin dataset version')
                    ids = []
                    for offset in range(0, 127, 20):
                        data = page.request.get(url + '/herbs', params={'family':'Leguminosae','limit':20,'offset':offset,'dataset_version':'local-2026-10-10'}).json()
                        ids.extend(r['source_record_id'] for r in data['records'])
                    assert len(ids) == len(set(ids)) == 127 and data['next_offset'] is None
                    results.append('all 127 records visited once across API pages')
                    row = page.locator('#results table tr').nth(1)
                    _, detail = click('查看记录', row.get_by_role('button', name='查看记录'))
                    assert detail['records'][0]['source_record_id'] == first['records'][0]['source_record_id']
                    results.append('filtered row opens exact source ID')

                    page.locator('#name').fill('HuaShi')
                    _, candidates = click('查详情')
                    assert candidates['status'] == 'ambiguous_entity'
                    assert page.locator('#results table tr').count() == 3
                    _, chosen = click('查看记录', page.locator('#results').get_by_role('button',name='查看记录').nth(1))
                    assert chosen['records'][0]['source_record_id'] == candidates['records'][1]['source_record_id']
                    assert candidates['records'][1]['source']['relative_file_path'] in page.locator('#detail').inner_text()
                    results.append('same-name candidate selection opens chosen record without merging')
                    page.locator('#names').fill('HuangQin,HuaShi,missing')
                    _, body = click('比较')
                    assert [x['status'] for x in body['herbs']] == ['ok','ambiguous_entity','not_found']
                    assert '同名候选' in page.locator('#results').inner_text() and '不可比较' in page.locator('#results').inner_text()
                    assert page.locator('#results summary').count() > 0
                    results.append('comparison table preserves ambiguous/missing states and evidence')
                    page.locator('#name').fill('黄芩')
                    _, body = click('查详情')
                    assert body['status'] == 'unsupported_name_mapping' and '暂不支持中文名称映射' in page.locator('#results').inner_text()
                    results.append('unsupported Chinese lookup has explanatory message')
                    page.locator('#conditions input').fill('__nonexistent__')
                    _, body = click('筛选')
                    assert body['count'] == 0 and '没有符合' in page.locator('#results').inner_text()
                    assert page.get_by_role('button',name='下一页',exact=True).is_disabled()
                    results.append('empty filter state disables pagination')
                    page.get_by_role('button',name='添加条件',exact=True).click()
                    page.locator('#conditions input').nth(1).fill('anything')
                    page.get_by_role('button',name='筛选',exact=True).click()
                    assert '每个字段只能使用一次' in page.locator('#status').inner_text()
                    results.append('duplicate filter fields rejected visibly')
                    page.locator('#conditions select').nth(1).select_option('flavors')
                    page.locator('#conditions input').first.fill('Leguminosae')
                    page.locator('#conditions input').nth(1).fill('Bitter')
                    _, body = click('筛选')
                    assert body['count'] > 0
                    assert all('bitter' in r['flavors'].lower() for r in body['records'])
                    results.append('multiple filter controls apply AND conditions')

                    malicious = json.loads(json.dumps(original))
                    malicious['records'][0]['fields']['name_pinyin']['value'] = '<img src=x onerror="window.injected=1">'
                    malicious['records'][0]['fields']['name_pinyin']['source']['raw_value'] = '<script>window.injected=1</script>'
                    page.route('**/herbs/MarkupProbe', lambda route: route.fulfill(json=malicious))
                    page.locator('#name').fill('MarkupProbe')
                    click('查详情')
                    page.locator('#detail summary').first.click()
                    assert '<img' in page.locator('#detail h2').inner_text()
                    assert '<script>' in page.locator('#detail pre').first.inner_text()
                    assert page.locator('#detail img, #detail script').count() == 0 and page.evaluate('window.injected') is None
                    results.append('values and raw evidence render as text without HTML execution (mock response)')
                    page.route('**/herbs/FailureProbe', lambda route: route.fulfill(status=503,json={'status':'error'}))
                    page.locator('#name').fill('FailureProbe')
                    click('查详情')
                    assert '资料库暂不可用' in page.locator('#status').inner_text()
                    assert page.locator('#detail').is_hidden()
                    results.append('service error visible without stale detail (mock response)')
                    page.locator('#name').fill('HuangQin')
                    click('查详情')
                    page.screenshot(path='/tmp/tcm-query-desktop.png',full_page=True)
                    page.set_viewport_size({'width':390,'height':844})
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                    page.screenshot(path='/tmp/tcm-query-mobile.png',full_page=True)
                    results.append('mobile page has no document horizontal overflow')
                    assert not errors, errors
                    results.append('no browser JavaScript errors')
                    browser.close()
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
    out = Path('audits/etcm2_mvp/page_workflow_2026-10-12.json')
    out.write_text(json.dumps({'passed': True, 'checks': results}, ensure_ascii=False, indent=2) + '\n')
    print(out.read_text())


if __name__ == '__main__':
    main()
