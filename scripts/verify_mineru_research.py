"""Browser acceptance checks against an already running local MinerU research service."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit


def run(url, output, executable=None):
    from playwright.sync_api import sync_playwright
    if urlsplit(url).hostname not in ('127.0.0.1', 'localhost'):
        raise ValueError('Only a local research service is supported')
    url = url.rstrip('/')
    report = {'status': 'running', 'checks': [], 'page_errors': [], 'external_requests': [],
              'human_signoff': False, 'clinical_accuracy_evaluated': False}
    def passed(name):
        report['checks'].append(name)
    try:
        with sync_playwright() as pw:
            options = {'headless': True}
            if executable:
                options['executable_path'] = str(executable)
            browser = pw.chromium.launch(**options)
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
            def guard(route):
                if route.request.url.startswith(url + '/') or route.request.url.startswith('blob:' + url + '/'):
                    route.continue_()
                else:
                    report['external_requests'].append(route.request.url)
                    route.abort()
            page.route('**/*', guard)
            page.goto(url)
            page.wait_for_function('()=>!document.getElementById("search").disabled')
            receipt = page.request.get(url + '/api/info').json()
            report.update(index_sha256=receipt['index_sha256'], view_sha256=receipt['view_sha256'],
                          entries=receipt['entries'], chunks=receipt['chunks'], browser_version=browser.version)
            assert receipt['entries'] == 616 and receipt['chunks'] == 8654
            assert '人工确认：未完成' in page.locator('#batch').inner_text()
            passed('current_snapshot_and_unreviewed_status')

            def lookup(question, method='name_field'):
                page.locator('#q').fill(question)
                page.locator('#method').select_option(method)
                page.locator('#search').click()
                page.wait_for_function('()=>!document.getElementById("search").disabled')

            lookup('木棉花的用量')
            assert page.locator('#results article').count() == 1
            assert '木棉花' in page.locator('#results h2').inner_text()
            card = page.locator('#results article')
            card.locator('summary').first.click()
            assert '物理第 86 页' in card.inner_text()
            card.get_by_role('button', name='查看原页').first.click()
            image = card.locator('img').first
            page.wait_for_function('(im)=>im.complete&&im.naturalWidth>0', arg=image.element_handle())
            passed('recovered_heading_and_original_pdf_preview')
            output.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output.parent / 'desktop.png'), full_page=True)

            lookup('大蓟炭的用量')
            card = page.locator('#results article')
            card.locator('summary').first.click()
            assert '全本 PDF · 物理第 76 页' in card.inner_text()
            card.get_by_role('button', name='查看原页').first.click()
            image = card.locator('img').first
            page.wait_for_function('(im)=>im.complete&&im.naturalWidth>0', arg=image.element_handle())
            passed('supplemental_full_pdf_source_is_not_high_pdf_offset')

            lookup('龙脷叶的用量')
            assert 'OCR 药名：龙腺叶 → AI 修订候选：龙脷叶' in page.locator('#results').inner_text()
            assert '未人工审核' in page.locator('#results').inner_text()
            card = page.locator('#results article')
            card.locator('summary').last.click()
            card.get_by_role('button', name='查看原页').first.click()
            image = card.locator('img').first
            page.wait_for_function('(im)=>im.complete&&im.naturalWidth>0', arg=image.element_handle())
            passed('raw_and_ai_revised_names_remain_unreviewed')

            lookup('麝香的用量')
            texts = page.locator('#results article').first.locator('pre').all_inner_texts()
            assert len(texts) == 2 and texts[0] != texts[1]
            assert '多人丸散用' in texts[0] and '多入丸散用' in texts[1]
            passed('raw_and_revised_field_texts_displayed_separately')

            for question, message in [('不存在的药功效', '未识别到药名'), ('木棉花价格', '未识别到字段'),
                                      ('木棉花注意事项', '不能推断其内容'),
                                      ('木棉花和木通的用量', '识别到多个药名')]:
                lookup(question)
                assert page.locator('#results article').count() == 0
                assert message in page.locator('#status').inner_text()
            passed('failed_queries_clear_previous_evidence')

            lookup('丹参配伍禁忌', 'bm25')
            assert page.locator('#results article').count() > 0
            passed('bm25_exploratory_search_available')
            for method in ('name_field', 'bm25'):
                lookup('酒大黄的用量', method)
                assert page.locator('#results article').count() == 0
                assert '炮制品范围未解析' in page.locator('#status').inner_text()
                assert '相关母条目来源' in page.locator('#unresolved').inner_text()
            parent = page.locator('#unresolved article').first
            parent.locator('summary').click()
            parent.get_by_role('button', name='查看原页').first.click()
            image = parent.locator('img').first
            page.wait_for_function('(im)=>im.complete&&im.naturalWidth>0', arg=image.element_handle())
            passed('unresolved_preparation_has_no_hits_and_parent_title_only')

            lookup('炙甘草的用量')
            assert page.locator('#results article').count() == 0
            assert '查询未完成' in page.locator('#status').inner_text()
            assert '0 个有效候选字段' in page.locator('#status').inner_text()
            assert '1 个未完成片段' in page.locator('#status').inner_text()
            assert '仅展示前' not in page.locator('#status').inner_text()
            pending = page.locator('#unresolved')
            assert '引用尚未展开' in pending.inner_text() and '同甘草' in pending.inner_text()
            pending.locator('summary').last.click()
            pending.get_by_role('button', name='查看原页').first.click()
            image = pending.locator('img').first
            page.wait_for_function('(im)=>im.complete&&im.naturalWidth>0', arg=image.element_handle())
            passed('unexpanded_reference_retains_both_original_source_fragments')
            page.screenshot(path=str(output.parent / 'reference_unresolved.png'), full_page=True)

            lookup('火星灵芝草的功效')
            assert page.locator('#results article').count() == 0
            assert page.locator('#unresolved article').count() == 0
            assert '未识别到药名' in page.locator('#status').inner_text()
            passed('unknown_compound_name_does_not_resolve_known_substring')
            for suffix in ['/api/search?q=x&q=y', '/api/search?k=21', '/api/search?method=bad',
                           '/api/page?chunk=x&kind=unknown', '/api/info?path=secret']:
                assert page.request.get(url + suffix).status == 400
            for suffix in ['/research.sqlite3', '/data/source.pdf', '/api/page?chunk=../../source.pdf',
                           '/%2e%2e/data/source.pdf']:
                assert page.request.get(url + suffix).status == 404
            assert page.request.post(url + '/api/search').status == 404
            passed('invalid_requests_and_arbitrary_file_access_rejected')

            lookup('<script>alert(1)</script>木棉花用量')
            assert page.locator('#results article').count() == 1
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(output.parent / 'mobile.png'), full_page=True)
            passed('text_safety_and_mobile_layout')
            assert not report['page_errors'] and not report['external_requests']
            browser.close()
            report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'checks': len(report['checks'])}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:7869')
    parser.add_argument('--out', type=Path, default=Path('var/mineru_research_ui/browser.json'))
    parser.add_argument('--executable', type=Path)
    args = parser.parse_args()
    run(args.url, args.out, args.executable)
