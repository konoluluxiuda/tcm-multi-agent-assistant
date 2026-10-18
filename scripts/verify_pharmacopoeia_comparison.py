"""Real-browser verification of the local dual-engine field review page."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

from tcm.pharmacopoeia import digest

ROOT = Path(__file__).resolve().parents[1]


def run(directory, output, executable=None):
    from playwright.sync_api import sync_playwright
    data = json.loads((directory/'comparison.json').read_text())
    report = {'human_signoff': False, 'checks': [], 'page_errors': [], 'external_requests': [],
              'comparison_sha256': digest(directory/'comparison.json'),
              'html_sha256': digest(directory/'index.html'), 'status': 'running'}
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args): pass
    def passed(name):
        report['checks'].append({'case': name, 'status': 'passed'})
    try:
        with ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(directory))) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            url = f'http://127.0.0.1:{server.server_port}'
            try:
                with sync_playwright() as pw:
                    options = {'headless': True}
                    if executable: options['executable_path'] = str(executable)
                    browser = pw.chromium.launch(**options)
                    report['browser_version'] = browser.version
                    page = browser.new_page(viewport={'width': 1280, 'height': 900})
                    page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                    def guard(route):
                        if route.request.url.startswith(url+'/'): route.continue_()
                        else:
                            report['external_requests'].append(route.request.url)
                            route.abort()
                    page.route('**/*', guard)
                    page.goto(url)
                    page.locator('#herb').wait_for(state='visible')
                    page.wait_for_function('!document.getElementById("herb").disabled')
                    for record in data['records']:
                        page.locator('#herb').select_option(record['id'])
                        assert page.locator('#fields article').count() == 9
                        assert '已批准0项' in page.locator('#status').inner_text()
                        assert page.locator('#fields article .alternative').count() == 18
                    passed('four_herbs_36_unreviewed_field_slots')
                    if data.get('visual_draft_provenance'):
                        total = 0
                        for record in data['records']:
                            page.locator('#herb').select_option(record['id'])
                            expected_drafts = [f for f in record['fields'] if 'ai_visual_draft' in f]
                            assert page.locator('.ai-draft').count() == len(expected_drafts)
                            total += len(expected_drafts)
                            for field in record['fields']:
                                assert field['reference_text'] is None and field['human_review'] == 'pending'
                                card = page.locator(f'article[data-field="{field["key"]}"]')
                                if 'ai_visual_draft' in field:
                                    assert card.locator('.ai-draft > pre').inner_text() == field['ai_visual_draft']['draft_text']
                                    assert '尚未人工审核' in card.locator('.ai-draft').inner_text()
                                for engine in ['rapidocr', 'paddle']:
                                    occurrences = field['engines'][engine]['occurrences']
                                    if occurrences:
                                        assert card.locator(f'[data-engine="{engine}"] > pre').inner_text() == '\n\n'.join(o['raw_text'] for o in occurrences)
                        expected_total = 33 if data['visual_draft_provenance'].get('scope') == 'all_available_fields' else 6
                        assert total == data['visual_draft_provenance']['draft_fields'] == expected_total
                        passed('ai_drafts_keep_ocr_and_human_references_separate')
                        if expected_total == 33:
                            observed = 0
                            for record in data['records']:
                                page.locator('#herb').select_option(record['id'])
                                for field in record['fields']:
                                    if 'ai_visual_observation' not in field: continue
                                    observed += 1
                                    card = page.locator(f'article[data-field="{field["key"]}"]')
                                    assert card.locator('.ai-draft').count() == 0
                                    assert '不表示无禁忌' in card.locator('.ai-observation').inner_text()
                                    assert field['reference_text'] is None
                            assert observed == 3
                            passed('missing_observations_stay_scoped_and_not_negative_safety_claims')
                        page.locator('#herb').select_option('DaFuPi')
                        card = page.locator('article[data-field="origin"]')
                        for engine in ['rapidocr', 'paddle']:
                            assert '·28·' not in card.locator(f'[data-engine="{engine}"] > pre').inner_text()
                        page.locator('.excluded-lines summary').click()
                        assert '·28·' in page.locator('.excluded-lines pre').inner_text()
                        passed('footer_excluded_from_origin_with_retained_evidence')
                    for record in data['records']:
                        page.locator('#herb').select_option(record['id'])
                        page.locator('#differences').check()
                        expected = [f for f in record['fields'] if f['comparison'] not in ('exact_agreement','normalized_agreement')]
                        assert page.locator('#fields article').count() == len(expected)
                        for field in expected:
                            if field['comparison'] == 'text_difference':
                                assert page.locator(f'article[data-field="{field["key"]}"] mark').count() > 0
                    passed('difference_filter_and_character_highlights')
                    page.locator('#differences').uncheck()
                    page.locator('#herb').select_option('DingGongTeng')
                    card = page.locator('article[data-field="cautions"]')
                    assert '孕妇禁用' in card.inner_text()
                    card.locator('.sources summary').click()
                    im = card.locator('.sources img').first
                    im.scroll_into_view_if_needed()
                    page.wait_for_function('(im)=>im.complete && im.naturalWidth>0', arg=im.element_handle())
                    assert 'PDF第25页' in card.inner_text() and '印刷第4页' in card.inner_text()
                    passed('continued_herb_caution_source_image')
                    page.locator('#herb').select_option('YiZhiHuangHua')
                    card = page.locator('article[data-field="cautions"]')
                    assert card.get_attribute('data-comparison') == 'both_unavailable'
                    assert '不表示无禁忌' in card.inner_text()
                    page.locator('#regions > details').first.locator('summary').click()
                    image = page.locator('#regions img').first
                    image.scroll_into_view_if_needed()
                    page.wait_for_function('(im)=>im.complete && im.naturalWidth>0', arg=image.element_handle())
                    passed('missing_cautions_and_full_region_fallback')
                    with page.expect_download() as event:
                        page.locator('#template').click()
                    download = event.value
                    template = json.loads(Path(download.path()).read_text())
                    assert len(template['fields']) == 36 and not template['human_signoff']
                    assert all(f['reference_text'] is None and f['reviewer'] is None for f in template['fields'])
                    assert template['comparison_sha256'] == digest(directory/'comparison.json')
                    passed('blank_reference_template_download_bound_to_artifact')
                    page.set_viewport_size({'width': 390, 'height': 844})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    passed('mobile_no_horizontal_page_overflow')
                    injected = json.loads((directory/'comparison.json').read_text())
                    payload = '<img src=x onerror="window.ocrExecuted=true">'
                    injected['records'][0]['fields'][0]['diff']['rapidocr'] = [{'text': payload, 'changed': True}]
                    for field in injected['records'][0]['fields']:
                        if 'ai_visual_draft' in field:
                            field['ai_visual_draft']['draft_text'] = payload
                    page.route('**/comparison.json', lambda route: route.fulfill(status=200, content_type='application/json', body=json.dumps(injected)))
                    page.reload()
                    page.wait_for_function('!document.getElementById("herb").disabled')
                    raw = page.locator('article[data-field="origin"] [data-engine="rapidocr"] > pre')
                    assert payload in raw.inner_text() and raw.locator('img').count() == 0
                    assert not page.evaluate('Boolean(window.ocrExecuted)')
                    if data.get('visual_draft_provenance'):
                        draft = page.locator('.ai-draft > pre').first
                        assert payload in draft.inner_text() and draft.locator('img').count() == 0
                    passed('ocr_markup_remains_literal_text')
                    page.unroute('**/comparison.json')
                    page.route('**/comparison.json', lambda route: route.fulfill(status=503, body='unavailable'))
                    page.reload()
                    page.wait_for_function('document.getElementById("status").textContent.includes("无法读取资料")')
                    assert page.locator('#fields article').count() == 0
                    assert page.locator('#herb').is_disabled()
                    passed('failed_load_has_no_stale_cards')
                    assert not report['page_errors'] and not report['external_requests']
                    browser.close()
            finally:
                server.shutdown()
                worker.join(timeout=10)
        report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'status': report['status'], 'checks': len(report['checks'])}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT/'var/pharmacopoeia_dual_review')
    parser.add_argument('--out', type=Path, default=ROOT/'var/pharmacopoeia_dual_review/browser.json')
    parser.add_argument('--executable', type=Path)
    args = parser.parse_args()
    run(args.directory, args.out, args.executable)
