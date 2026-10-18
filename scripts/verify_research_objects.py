"""Verify source-bound object annotations using an isolated browser session."""
import argparse
import hashlib
import json
import os
from pathlib import Path


def verify(url, output, browser_path):
    from playwright.sync_api import sync_playwright
    from tcm.mineru_report import literal
    output.mkdir(parents=True,mode=0o700,exist_ok=False)
    report={'status':'running','checks':[],'url':url,'page_errors':[],
            'clinical_quality_evaluated':False,'object_identity_approved':False}
    def passed(label):report['checks'].append(label);print(label+': passed',flush=True)
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,executable_path=str(browser_path))
            context=browser.new_context(viewport={'width':1280,'height':1000},accept_downloads=True)
            page=context.new_page();page.set_default_timeout(60000)
            page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            submitted=[]
            page.on('request',lambda r:submitted.append(r.post_data_json) if r.url==url+'/api/evidence/report' else None)
            page.goto(url)
            def idle():page.wait_for_function('()=>!document.getElementById("message-input").disabled')
            def snapshot():return context.request.get(url+'/conversation').json()
            idle()
            response=context.request.post(url+'/chat',data={'query':'/pubmed Astragalus membranaceus[Title/Abstract] AND randomized controlled trial[Publication Type]'})
            assert response.status==200;result=response.json()['pubmed'];batch=result['tool_result']
            assert len(result['object_scope_checks'])==len(batch['articles'])
            assert all(not c['object_identity_verified'] and not c['direct_attribution_allowed'] for c in result['object_scope_checks'])
            page.reload();idle()
            card=page.locator('#chat-body [data-pubmed-status="ok"]').last;card.wait_for()
            assert card.locator('.object-scope-signals').count()==len(batch['articles'])
            passed('real_batch_scope_hints_are_separate_from_identity_and_survive_reload')
            card.get_by_text('导出药典＋文献证据清单',exact=True).click()
            form=card.locator('.evidence-bundle-form');query=form.get_by_label('药典查询：')
            form.locator('.object-review > summary').click()
            row=form.locator('[data-review-pmid]').first
            enabled=row.locator('input[type=checkbox]');enabled.click();assert not enabled.is_checked()
            assert '先填写' in form.locator('.object-review').inner_text()
            passed('annotation_requires_an_explicit_local_query')
            query.fill('黄芪的功效');enabled.check()
            row.get_by_role('button',name='引用所选原文前300字').click()
            quote=row.locator('textarea').input_value();assert 12<=len(quote)<=300
            before=snapshot()
            with page.expect_download() as download:form.get_by_role('button',name='下载证据清单',exact=True).click()
            file=output/'annotated-evidence.md';download.value.save_as(file);os.chmod(file,0o600)
            text=file.read_text();body=submitted[-1]
            assert body['object_reviews'][0]['quote']==quote and body['object_review_target']=='黄芪的功效'
            assert literal(quote) in text and '用户对象标注' in text and '对象身份核验：全部未完成' in text
            assert '药典锚点' in text and hashlib.sha256(batch['articles'][0]['title'].encode()).hexdigest() in text
            assert snapshot()==before
            passed('quoted_annotation_download_is_bound_to_query_source_and_current_batch_without_history_write')
            query.fill('黄芪的用量');assert not enabled.is_checked() and row.locator('textarea').input_value()==''
            assert '取消旧对象标注' in form.locator('.object-review').inner_text()
            passed('changing_local_query_discards_stale_annotation_selection')
            query.fill('黄芪的功效');enabled.check()
            if batch['articles'][0]['abstract']:
                row.locator('select').nth(2).select_option('1');assert row.locator('textarea').input_value()==''
                row.get_by_role('button',name='引用所选原文前300字').click()
                assert row.locator('textarea').input_value()==batch['articles'][0]['abstract'][0]['text'][:300]
            passed('source_selector_preserves_abstract_section_identity_and_resets_quote')
            invalid=json.loads(json.dumps(body));invalid['object_reviews'][0]['quote']='This fabricated source excerpt is not in the article.'
            assert context.request.post(url+'/api/evidence/report',data=invalid).status==400
            invalid=json.loads(json.dumps(body));invalid['object_review_target']='甘草的功效'
            assert context.request.post(url+'/api/evidence/report',data=invalid).status==400
            invalid=json.loads(json.dumps(body));invalid['object_reviews'][0]['approved']=True
            assert context.request.post(url+'/api/evidence/report',data=invalid).status==422
            stranger=browser.new_context()
            assert stranger.request.post(url+'/api/evidence/report',data=body).status==401
            stranger.close()
            passed('fabricated_quote_stale_query_approval_field_and_other_cookie_are_rejected')
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1')
            page.screenshot(path=str(output/'mobile.png'),full_page=True)
            passed('mobile_annotation_controls_do_not_overflow')
            page.reload();idle()
            card=page.locator('#chat-body [data-pubmed-status="ok"]').last
            assert card.locator('.object-scope-signals').count()==len(batch['articles'])
            assert card.locator('.object-review input[type=checkbox]:checked').count()==0
            passed('reload_keeps_source_scope_hints_but_does_not_persist_user_annotations')
            assert not report['page_errors'],report['page_errors']
            context.request.delete(url+'/conversation');browser.close()
            report.update(status='passed',annotated_export_sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
                          article_count=len(batch['articles']))
    except Exception as exc:
        report.update(status='failed',failure_type=type(exc).__name__);raise
    finally:(output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--url',default='http://127.0.0.1:7873')
    parser.add_argument('--out',type=Path,required=True);parser.add_argument('--browser',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(verify(args.url,args.out.resolve(),args.browser),ensure_ascii=False,indent=2))
