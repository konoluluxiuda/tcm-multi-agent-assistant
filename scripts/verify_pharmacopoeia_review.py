"""Real Chromium checks of generated local field cards and source crops; no model calls."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from tcm.pharmacopoeia import digest

ROOT=Path(__file__).resolve().parents[1]


def run(directory,output,executable=None):
    from playwright.sync_api import sync_playwright
    report={'human_signoff':False,'checks':[],'page_errors':[],'blocked_external_requests':[],
            'candidates_sha256':digest(directory/'candidates.json'),
            'code_sha256':{s:digest(ROOT/s) for s in ['tcm/pharmacopoeia_review.html','scripts/build_pharmacopoeia_candidates.py','scripts/verify_pharmacopoeia_review.py']}}
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self,*args):pass
    with ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(directory))) as server:
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        url=f'http://127.0.0.1:{server.server_port}'
        try:
            with sync_playwright() as pw:
                args={'headless':True}
                if executable:args['executable_path']=str(executable)
                browser=pw.chromium.launch(**args)
                report['browser_version']=browser.version
                page=browser.new_page(viewport={'width':1280,'height':900})
                page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
                def guard(route):
                    if route.request.url.startswith(url+'/'):route.continue_()
                    else:
                        report['blocked_external_requests'].append(route.request.url);route.abort()
                page.route('**/*',guard);page.goto(url)
                page.locator('#search').wait_for(state='visible')
                def lookup(name,field):
                    page.locator('#name').fill(name);page.locator('#field').select_option(field)
                    page.locator('#search').click()
                    return page.locator('#result article')
                card=lookup('一枝黄花','medicinal_part')
                assert card.locator('section > pre').inner_text()=='干燥全草'
                assert card.get_attribute('data-review-status')=='quarantined'
                card.locator('section summary').click()
                image=card.locator('section img').first;image.scroll_into_view_if_needed()
                page.wait_for_function('(img)=>img.complete && img.naturalWidth>0',arg=image.element_handle())
                assert '印刷第3页' in card.inner_text()
                report['checks'].append({'case':'field_card_and_original_crop','status':'passed'})
                card=lookup('丁公藤','cautions')
                assert '孕妇禁用' in card.locator('section > pre').inner_text()
                assert card.get_attribute('data-review-status')=='pending_human_review'
                card.locator('section summary').click()
                image=card.locator('section img').first;image.scroll_into_view_if_needed()
                page.wait_for_function('(img)=>img.complete && img.naturalWidth>0',arg=image.element_handle())
                assert 'PDF第25页' in card.inner_text() and '印刷第4页' in card.inner_text()
                report['checks'].append({'case':'cross_page_entry_caution_source','status':'passed'})
                card=lookup('山麦冬','origin')
                assert 'spicala' in card.locator('section > pre').inner_text()
                assert card.get_attribute('data-review-status')=='quarantined'
                report['checks'].append({'case':'quarantine_preserves_taxon_error','status':'passed'})
                card=lookup('大腹皮','pinyin')
                assert card.locator('section > pre').inner_text()=='dnpa'
                report['checks'].append({'case':'quarantine_preserves_pinyin_error','status':'passed'})
                card=lookup('一枝黄花','cautions')
                assert card.get_attribute('data-field-state')=='not_observed_in_selected_ocr'
                assert '不表示无禁忌' in card.inner_text() and card.locator('section').count()==0
                report['checks'].append({'case':'missing_does_not_become_no_contraindication','status':'passed'})
                lookup('不在样本里','origin')
                assert page.locator('#result article').count()==0
                assert '未找到' in page.locator('#status').inner_text()
                report['checks'].append({'case':'unknown_entity_no_stale_card','status':'passed'})
                injected=json.loads((directory/'candidates.json').read_text())
                payload='<img src=x onerror="window.ocrExecuted=true">'
                injected['records'][0]['fields']['origin']['occurrences'][0]['raw_text']=payload
                page.route('**/candidates.json',lambda route:route.fulfill(status=200,content_type='application/json',body=json.dumps(injected)))
                page.reload();page.locator('#search').click()
                assert payload in page.locator('#result section > pre').inner_text()
                assert not page.evaluate('Boolean(window.ocrExecuted)')
                assert page.locator('#result section pre img').count()==0
                report['checks'].append({'case':'ocr_html_is_literal_text','status':'passed'})
                assert not report['page_errors'] and not report['blocked_external_requests']
                browser.close()
        finally:
            server.shutdown();worker.join(timeout=10)
    report['status']='passed';output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':'passed','checks':len(report['checks'])}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',type=Path,default=ROOT/'var/pharmacopoeia_candidates')
    p.add_argument('--out',type=Path,default=ROOT/'var/pharmacopoeia_candidates/browser.json')
    p.add_argument('--executable',type=Path)
    a=p.parse_args();run(a.directory,a.out,a.executable)
