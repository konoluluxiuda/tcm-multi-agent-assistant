"""Exercise local quality UI and read-only APIs against the real research batch."""
import argparse
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading

from scripts.serve_pharmacopoeia_research import make_handler, ROOT
from tcm.pharmacopoeia import digest


def run(directory, policy, output, executable=None):
    from playwright.sync_api import sync_playwright
    report = {'status': 'running', 'human_signoff': False, 'checks': [], 'page_errors': [], 'external_requests': [],
              'batch_sha256': digest(directory/'quality.json'), 'html_sha256': digest(directory/'index.html')}
    before = digest(directory/'research.sqlite3')
    def passed(name): report['checks'].append({'case': name, 'status': 'passed'})
    try:
        with tempfile.TemporaryDirectory(prefix='tcm-quality-policy-') as temporary:
            local_policy = Path(temporary)/'policy.json'; local_policy.write_bytes(policy.read_bytes())
            with ThreadingHTTPServer(('127.0.0.1', 0), make_handler(directory, local_policy)) as server:
                worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
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
                            else: report['external_requests'].append(route.request.url); route.abort()
                        page.route('**/*', guard); page.goto(url)
                        page.wait_for_function('()=>!document.getElementById("search").disabled')
                        assert '机器检查通过28个' in page.locator('#batch').inner_text()
                        assert page.locator('#queue article').count() == 5
                        assert page.locator('#samples article').count() == 6
                        passed('quality_counts_exception_queue_and_sampling')
                        def lookup(q='', herb='', field=''):
                            page.locator('#q').fill(q); page.locator('#herb').select_option(herb)
                            page.locator('#field').select_option(field); page.locator('#search').click()
                            page.wait_for_function('()=>!document.getElementById("status").textContent.includes("检索中")')
                        lookup('孕妇')
                        assert page.locator('#results article').count() == 1
                        assert '孕妇禁用' in page.locator('#results article pre').inner_text()
                        assert '人工审核：pending' in page.locator('#results').inner_text()
                        passed('two_character_query_returns_unreviewed_source_excerpt')
                        card = page.locator('#results article'); card.locator('summary').click()
                        im = card.locator('img').first; im.scroll_into_view_if_needed()
                        page.wait_for_function('(im)=>im.complete&&im.naturalWidth>0', arg=im.element_handle())
                        assert 'PDF第25页' in card.inner_text() and '印刷第4页' in card.inner_text()
                        passed('continued_herb_original_page_citation')
                        lookup(herb='ShanMaiDong', field='origin')
                        assert page.locator('#results article').count() == 0
                        assert '不能推断' in page.locator('#results').inner_text()
                        lookup(herb='YiZhiHuangHua', field='cautions')
                        assert page.locator('#results article').count() == 0
                        passed('held_and_missing_fields_not_answered_or_promoted_by_ai')
                        lookup('归脾')
                        assert page.locator('#results article').count() == 1
                        assert '大腹皮' in page.locator('#results').inner_text()
                        passed('trigram_chinese_search')
                        lookup('丁公藤')
                        assert page.locator('#results article').count() > 1
                        assert all('丁公藤' in text for text in page.locator('#results article h3').all_inner_texts())
                        passed('exact_herb_keyword_routes_to_candidate_metadata')
                        for suffix in ['/api/search?q=x&q=y', '/api/search?field=unknown', '/api/search?q=x&limit=100']:
                            assert page.request.get(url+suffix).status == 400
                        assert page.request.get(url+'/research.sqlite3').status == 404
                        assert page.request.get(url+'/assets/%2e%2e/quality.json').status == 404
                        assert page.request.post(url+'/api/search').status == 501
                        passed('invalid_queries_and_file_exposure_blocked')
                        page.set_viewport_size({'width':390,'height':844})
                        assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth')
                        passed('mobile_no_horizontal_page_overflow')
                        changed = json.loads(local_policy.read_text()); changed['suspended_source_sha256'] = ['test-suspension']
                        local_policy.write_text(json.dumps(changed))
                        assert page.request.get(url+'/api/search?q=孕妇').status == 409
                        lookup('孕妇')
                        assert page.locator('#results article').count() == 0
                        assert '重建批次' in page.locator('#status').inner_text()
                        passed('changed_policy_refuses_stale_index_and_clears_results')
                        assert not report['page_errors'] and not report['external_requests']
                        browser.close()
                finally:
                    server.shutdown(); worker.join(timeout=10)
        assert digest(directory/'research.sqlite3') == before
        passed('database_unchanged_after_requests')
        report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'status':report['status'],'checks':len(report['checks'])}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT/'var/pharmacopoeia_research')
    parser.add_argument('--policy', type=Path, default=ROOT/'eval/pharmacopoeia_qc_policy_v1.json')
    parser.add_argument('--out', type=Path, default=ROOT/'var/pharmacopoeia_research/browser.json')
    parser.add_argument('--executable', type=Path)
    args = parser.parse_args(); run(args.directory, args.policy, args.out, args.executable)
