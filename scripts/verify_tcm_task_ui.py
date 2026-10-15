"""Real Chromium execution of candidate UI workflows and evidence notices.

Optional browser dependencies: use the environment documented in OFFLINE_CI.md
and install Playwright/Chromium separately. This is not a human signoff.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit, parse_qs, unquote

from scripts.eval_tcm_golden import database_preflight
from scripts.validate_tcm_task_candidates import validate

ROOT = Path(__file__).resolve().parents[1]
LABELS = {'flavors': '味', 'medicinal_part': '药用部位', 'properties': '性',
          'reference_pmids': '关联 PubMed ID'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(suite_path, database, output, executable=None):
    import httpx
    from playwright.sync_api import sync_playwright

    raw = suite_path.read_bytes()
    suite = json.loads(raw)
    checked = validate(suite)
    if checked['errors']:
        raise ValueError('; '.join(checked['errors']))
    database_preflight(database, {'dataset_version': suite['dataset_version'], 'snapshot': suite['source_snapshot']})
    initial_db = sha(database)
    report = {'suite_sha256': hashlib.sha256(raw).hexdigest(), 'dataset_version': suite['dataset_version'],
              'method': 'real Chromium + temporary loopback Uvicorn server', 'cases': [], 'evidence_notice_checks': [],
              'degradation_checks': [],
              'human_signoff': False, 'task_completion_rate': None, 'external_page_requests': [],
              'source_code_sha256': {name: sha(ROOT / name) for name in ('tcm/api.py', 'tcm/web.html', 'scripts/verify_tcm_task_ui.py')},
              'limitations': ['Browser automation is not human review or scientific validation.',
                              'Returning means scrolling to the still-visible results; browser Back/reload/persistence not tested.']}
    output.mkdir(parents=True, exist_ok=True)
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'TMP', 'TEMP') if key in os.environ}
    env.update(TCM_DB=str(database.resolve()), TCM_DATASET_VERSION=suite['dataset_version'])
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        with subprocess.Popen([sys.executable, '-m', 'uvicorn', 'tcm.api:app', '--fd', str(sock.fileno())],
                              cwd=ROOT, env=env, pass_fds=(sock.fileno(),), stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL) as server:
            try:
                url = f'http://127.0.0.1:{port}'
                deadline = time.monotonic() + 15
                with httpx.Client(trust_env=False, timeout=.5) as client:
                    while True:
                        try:
                            ready = client.get(url + '/ready')
                            if ready.status_code == 200:
                                assert ready.json() == {'status': 'ready', 'dataset_version': suite['dataset_version'],
                                                        'record_count': suite['source_snapshot']['record_count']}
                                break
                        except httpx.HTTPError:
                            pass
                        if server.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError('Temporary API server did not become ready')
                        time.sleep(.1)
                with sync_playwright() as pw:
                    options = {'headless': True}
                    if executable:
                        options['executable_path'] = str(executable)
                    browser = pw.chromium.launch(**options)
                    report['browser_version'] = browser.version
                    context = browser.new_context(viewport={'width': 1280, 'height': 900})
                    context.set_default_timeout(10000)

                    def guard(route):
                        if urlsplit(route.request.url).netloc == f'127.0.0.1:{port}':
                            route.continue_()
                        else:
                            report['external_page_requests'].append(route.request.url)
                            route.abort()
                    context.route('**/*', guard)

                    def page_for():
                        page = context.new_page()
                        page.goto(url)
                        return page

                    def click(page, button, endpoint):
                        with page.expect_response(lambda r: unquote(urlsplit(r.url).path) == endpoint) as pending:
                            button.click()
                        response = pending.value
                        assert response.status == 200, response.status
                        page.wait_for_function("document.getElementById('status').textContent !== '查询中…'")
                        return response, response.json()

                    def controls(page):
                        rows = page.locator('#conditions .filter-row')
                        return {rows.nth(i).locator('select').input_value(): rows.nth(i).locator('input').input_value()
                                for i in range(rows.count())}

                    def fill_conditions(page, filters):
                        rows = page.locator('#conditions .filter-row')
                        while rows.count() < len(filters):
                            page.get_by_role('button', name='添加条件', exact=True).click()
                        assert rows.count() == len(filters)
                        for index, (key, value) in enumerate(filters.items()):
                            rows.nth(index).locator('select').select_option(key)
                            rows.nth(index).locator('input').fill(value)

                    def filter_stage(page, filters, expected):
                        fill_conditions(page, filters)
                        response, body = click(page, page.get_by_role('button', name='筛选', exact=True), '/herbs')
                        seen, requests = [], []
                        while True:
                            query = parse_qs(urlsplit(response.url).query)
                            for key, value in filters.items():
                                assert query[key] == [value]
                            offset = body['offset']
                            page_ids = [r['source_record_id'] for r in body['records']]
                            assert body['status'] == 'ok' and body['count'] == expected['count']
                            assert body['dataset_version'] == suite['dataset_version']
                            assert page_ids == expected['all_matching_source_record_ids'][offset:offset+20]
                            assert page.locator('#results table tr').count() == len(page_ids) + 1
                            assert page.locator('#results h2').inner_text() == f"筛选结果 · {expected['count']} 条来源记录"
                            for index, record in enumerate(body['records']):
                                text = page.locator('#results table tr').nth(index+1).inner_text()
                                assert record['source']['relative_file_path'] in text
                                assert record['name_pinyin'] in text
                            seen.extend(page_ids)
                            requests.append({'query': query, 'offset': offset, 'source_ids': page_ids})
                            if body['next_offset'] is None:
                                assert page.get_by_role('button', name='下一页', exact=True).is_disabled()
                                break
                            response, body = click(page, page.get_by_role('button', name='下一页', exact=True), '/herbs')
                        assert seen == expected['all_matching_source_record_ids'] and len(seen) == len(set(seen))
                        while body['offset']:
                            response, body = click(page, page.get_by_role('button', name='上一页', exact=True), '/herbs')
                        assert controls(page) == filters
                        return body, requests

                    for case in suite['cases']:
                        if case['execution_mode'] != 'manual_ui':
                            continue
                        page = page_for()
                        errors = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        item = {'id': case['id'], 'browser_status': 'failed', 'human_review': 'pending',
                                'task_status': 'pending_human_review', 'stages': []}
                        try:
                            expected, steps = case['expected'], case['input']['steps']
                            for index in range(2):
                                filters = steps[index]['filters']
                                body, requests = filter_stage(page, filters, expected['steps'][index])
                                item['stages'].append({'filters': filters, 'count': body['count'], 'requests': requests,
                                                       'complete_result_set_checked': True})
                            ids = [r['source_record_id'] for r in body['records']]
                            selected = expected['open_source_record_id']
                            index = ids.index(selected)
                            row = page.locator('#results table tr').nth(index+1)
                            response, detail = click(page, row.get_by_role('button', name='查看记录', exact=True),
                                                     '/herb-records/' + selected)
                            assert parse_qs(urlsplit(response.url).query)['dataset_version'] == [suite['dataset_version']]
                            assert detail['records'][0]['source_record_id'] == selected
                            field = expected['source_field']
                            field_row = page.locator('#detail tr').filter(has=page.get_by_text(LABELS[field], exact=True))
                            assert field_row.count() == 1
                            field_row.locator('summary').click()
                            assert field_row.locator('details').get_attribute('open') is not None
                            text = field_row.locator('pre').inner_text()
                            anchor = expected['source_evidence']
                            for value in (anchor['relative_path'], anchor['sha256'], anchor['json_pointer'],
                                          json.dumps(anchor['raw_value'], ensure_ascii=False, indent=2)):
                                assert value in text
                            shot = case['id'] + '-source.png'
                            page.screenshot(path=str(output / shot), full_page=True)
                            before_rows = page.locator('#results table').inner_text()
                            # The current single-page UI keeps the list above detail.
                            page.locator('#results').scroll_into_view_if_needed()
                            assert page.locator('#results').is_visible()
                            assert controls(page) == steps[1]['filters']
                            assert page.locator('#results table').inner_text() == before_rows
                            assert not errors, errors
                            item.update(browser_status='passed', opened_source_record_id=selected, evidence=anchor,
                                        source_details_expanded=True, return_action='scroll_to_retained_results',
                                        filter_controls_and_rows_retained=True, screenshot=shot,
                                        javascript_errors=errors)
                        except Exception as exc:
                            item['error'] = f'{type(exc).__name__}: {exc}'
                            page.screenshot(path=str(output / (case['id'] + '-failed.png')), full_page=True)
                        report['cases'].append(item)
                        print(case['id'], item['browser_status'], flush=True)
                        page.close()

                    # Check only displayed evidence limits, not the science itself.
                    for case in suite['cases']:
                        if case['category'] != 'sparse_evidence' or case.get('evidence_focus') != 'pmid':
                            continue
                        page = page_for()
                        item = {'id': case['id'], 'browser_status': 'failed', 'scientific_conclusions': 'not_verified'}
                        try:
                            source_id = case['input']['source_record_id']
                            response = page.request.get(url + '/herb-records/' + source_id,
                                                        params={'dataset_version': suite['dataset_version']})
                            body = response.json()
                            # Navigate through the actual name form, then inspect the rendered field.
                            page.locator('#name').fill(body['records'][0]['fields']['name_pinyin']['value'])
                            _, viewed = click(page, page.get_by_role('button', name='查详情', exact=True),
                                              '/herbs/' + body['records'][0]['fields']['name_pinyin']['value'])
                            assert viewed['status'] == 'ok' and viewed['records'][0]['source_record_id'] == source_id
                            assert '源资料内容未核验，关联文献不代表每项属性均获证实' in page.locator('header').inner_text()
                            assert '来源内容未核验' in page.locator('#detail').inner_text()
                            row = page.locator('#detail tr').filter(has=page.get_by_text(LABELS['reference_pmids'], exact=True))
                            wanted = case['expected']['fields']['reference_pmids']
                            if wanted['state'] == 'missing':
                                assert '未提供' in row.inner_text() and row.locator('summary').count() == 0
                            else:
                                assert row.locator('td').nth(1).locator('div').first.inner_text() == '；'.join(wanted['value'])
                                row.locator('summary').click()
                                assert 'SHA-256' in row.locator('pre').inner_text()
                            item.update(browser_status='passed', disclaimer_visible=True,
                                        pmid_display_state=wanted['state'], paper_content='not_reviewed')
                        except Exception as exc:
                            item['error'] = f'{type(exc).__name__}: {exc}'
                        report['evidence_notice_checks'].append(item)
                        print(case['id'], 'notice', item['browser_status'], flush=True)
                        page.close()
                    for case in suite['cases']:
                        if 'api_contract' not in case['expected']:
                            continue
                        page = page_for()
                        item = {'id': case['id'], 'browser_status': 'failed', 'requested_capability': 'unsupported'}
                        try:
                            name = case['input']['name']
                            page.locator('#name').fill(name)
                            _, body = click(page, page.get_by_role('button', name='查详情', exact=True), '/herbs/' + name)
                            assert body['status'] == 'unsupported_name_mapping' and body['records'] == []
                            assert 'chinese_names_unmapped' in body['warnings']
                            assert '暂不支持中文名称映射' in page.locator('#results').inner_text()
                            assert page.locator('#detail').is_hidden() and page.locator('#results table').count() == 0
                            item.update(browser_status='passed', degradation_message_visible=True, incorrect_records_shown=False)
                        except Exception as exc:
                            item['error'] = f'{type(exc).__name__}: {exc}'
                        report['degradation_checks'].append(item)
                        print(case['id'], 'degradation', item['browser_status'], flush=True)
                        page.close()
                    response = context.request.get(url + '/herbs', params={'family': 'Apiaceae', 'limit': 0})
                    payload = response.json()
                    report['parameter_contract_check'] = {'request': {'family': 'Apiaceae', 'limit': 0},
                                                          'http_status': response.status, 'body': payload,
                                                          'scope': 'Actual HTTP parameter response, not the draft explanatory contract text.'}
                    report['parameter_contract_check']['passed'] = response.status == 422 and any(
                        value.get('loc') == ['query', 'limit'] for value in payload.get('detail', []))
                    context.close()
                    browser.close()
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
    report['inputs_unchanged'] = sha(database) == initial_db and suite_path.read_bytes() == raw
    rows = report['cases'] + report['evidence_notice_checks'] + report['degradation_checks']
    report['summary'] = {'ui_cases': len(report['cases']), 'notice_cases': len(report['evidence_notice_checks']),
                         'degradation_cases': len(report['degradation_checks']),
                         'passed': sum(r['browser_status'] == 'passed' for r in rows),
                         'failed': sum(r['browser_status'] == 'failed' for r in rows)}
    report['status'] = 'passed' if report['summary']['failed'] == 0 and report['parameter_contract_check']['passed'] and report['inputs_unchanged'] and not report['external_page_requests'] else 'failed'
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, default=ROOT / 'eval/tcm_task_candidates_v1.json')
    parser.add_argument('--db', type=Path, default=ROOT / 'var/tcm/herbs.sqlite3')
    parser.add_argument('--out', type=Path, default=ROOT / 'var/task_ui')
    parser.add_argument('--browser-executable', type=Path)
    args = parser.parse_args()
    if args.out.resolve() in (args.suite.resolve(), args.db.resolve()) or args.out.resolve() in args.suite.resolve().parents or args.out.resolve() in args.db.resolve().parents:
        parser.error('output must not contain input files')
    report = run(args.suite, args.db, args.out, args.browser_executable)
    print(json.dumps({'status': report['status'], 'summary': report['summary']}, ensure_ascii=False))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
