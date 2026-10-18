"""Browser acceptance of the three research views using a new private test session."""
import argparse
import hashlib
import json
import os
from pathlib import Path


def verify(url, output, browser_path):
    from playwright.sync_api import sync_playwright
    output.mkdir(parents=True,mode=0o700,exist_ok=False)
    report={'status':'running','checks':[],'page_errors':[],'url':url,'clinical_quality_evaluated':False}
    def passed(label):
        report['checks'].append(label);print(label+': passed',flush=True)
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,executable_path=str(browser_path))
            context=browser.new_context(viewport={'width':1280,'height':1000},accept_downloads=True)
            page=context.new_page();page.set_default_timeout(60000)
            page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
            chats=[]
            page.on('request',lambda r:chats.append(r.post_data_json) if r.url==url+'/chat' and r.method=='POST' else None)
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            page.goto(url)
            def idle():page.wait_for_function('()=>!document.getElementById("message-input").disabled')
            def tab(name):page.get_by_role('tab',name=name,exact=True).click()
            def snapshot():return context.request.get(url+'/conversation').json()
            idle();assert page.get_by_role('tab').count()==3
            assert page.get_by_role('tabpanel').count()==1
            before=snapshot();old_count=len(chats)
            page.locator('#message-input').fill('尚未发送的聊天草稿')
            tab('药材与药典')
            form=page.locator('[data-workspace-herb-form]')
            form.locator('[name=herb_name]').fill('黄芪');form.locator('[name=field]').select_option(label='用量')
            tab('现代文献')
            literature=page.locator('[data-workspace-literature-form]')
            term='Astragalus membranaceus[Title/Abstract] AND randomized controlled trial[Publication Type]'
            literature.locator('[name=term]').fill(term)
            tab('数据概览');tab('药材与药典')
            assert form.locator('[name=herb_name]').input_value()=='黄芪'
            assert form.locator('[name=field]').input_value()=='用量'
            tab('现代文献');assert literature.locator('[name=term]').input_value()==term
            assert page.locator('#message-input').input_value()=='尚未发送的聊天草稿'
            assert len(chats)==old_count and snapshot()==before
            passed('three_views_preserve_drafts_and_do_not_call_tools_or_change_server_state')
            page.get_by_role('tab',name='现代文献',exact=True).focus();page.keyboard.press('Home')
            assert page.get_by_role('tab',name='数据概览',exact=True).get_attribute('aria-selected')=='true'
            page.keyboard.press('ArrowRight')
            assert page.get_by_role('tab',name='药材与药典',exact=True).get_attribute('aria-selected')=='true'
            page.keyboard.press('End')
            assert page.get_by_role('tab',name='现代文献',exact=True).get_attribute('aria-selected')=='true'
            passed('keyboard_tabs_selection_and_single_visible_panel')
            tab('数据概览');page.get_by_role('button',name='更新数据概览',exact=True).click();idle()
            overview=page.locator('[data-overview-result]')
            assert '2076' in overview.inner_text() and '616' in overview.inner_text()
            assert overview.locator('li').count()==20
            assert chats[-1]['query']=='数据概览'
            assert page.locator('#message-input').input_value()=='尚未发送的聊天草稿'
            passed('live_overview_and_classifications_render_in_panel_without_overwriting_chat_draft')
            tab('药材与药典');form.get_by_role('button',name='查询资料',exact=True).click();idle()
            assert chats[-1]['query']=='/research 黄芪的用量'
            assert page.locator('#chat-body [data-research-status="candidates"]').count()>0
            assert '黄芪' in page.locator('[data-workspace-status]').inner_text()
            assert form.locator('[name=herb_name]').input_value()=='黄芪'
            passed('pharmacopoeia_form_routes_selected_name_and_field_and_keeps_draft')
            form.locator('[name=source]').select_option('herb');assert form.locator('[name=field]').is_disabled()
            form.locator('[name=herb_name]').fill('BaiBu');form.get_by_role('button',name='查询资料',exact=True).click();idle()
            assert chats[-1]['query']=='/herb BaiBu' and 'ETCM' in page.locator('[data-workspace-status]').inner_text()
            form.locator('[name=source]').select_option('research');assert form.locator('[name=field]').is_enabled()
            passed('explicit_etcm_source_does_not_silently_apply_pharmacopoeia_fields')
            tab('现代文献');assert literature.locator('[name=term]').input_value()==term
            literature.get_by_role('button',name='检索文献',exact=True).click();idle()
            assert chats[-1]['query']=='/pubmed '+term
            card=page.locator('#chat-body [data-pubmed-status="ok"]').last;card.wait_for()
            assert '文献批次' in page.locator('[data-workspace-status]').inner_text()
            card.get_by_text('导出药典＋文献证据清单',exact=True).click()
            export=card.locator('.evidence-bundle-form');export.get_by_label('药典查询：').fill('黄芪的功效')
            with page.expect_download() as download:export.get_by_role('button',name='下载证据清单').click()
            target=output/'evidence.md';download.value.save_as(target);os.chmod(target,0o600)
            assert '研究对象对应关系尚未核验' in target.read_text()
            report['download_sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
            passed('real_literature_form_and_combined_evidence_download')
            before=snapshot();old_count=len(chats)
            for name in ('数据概览','药材与药典','现代文献'):tab(name)
            assert snapshot()==before and len(chats)==old_count
            page.reload();idle()
            assert snapshot()==before and '文献批次' in page.locator('[data-workspace-status]').inner_text()
            passed('view_switch_and_reload_preserve_committed_task_and_literature_focus')
            page.set_viewport_size({'width':390,'height':844})
            for name in ('数据概览','药材与药典','现代文献'):
                tab(name);assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1'),name
            page.screenshot(path=str(output/'mobile.png'),full_page=True)
            page.set_viewport_size({'width':1280,'height':1000});tab('药材与药典')
            page.screenshot(path=str(output/'desktop.png'),full_page=True)
            passed('mobile_width_and_desktop_panels')
            tab('数据概览')
            def malicious(route):
                route.fulfill(json={'status':'success','response':'合成安全测试','agent':'ASSISTANT_INFO_AGENT',
                    'assistant':{'topic':'overview','herbs':{'raw_efficacy_values':[{'value':'<img src=x onerror="window.badWorkspace=1">','record_count':1}]}}})
            page.route('**/chat',malicious)
            page.get_by_role('button',name='更新数据概览',exact=True).click();idle()
            assert overview.locator('img').count()==0 and '<img' in overview.inner_text()
            assert page.evaluate('window.badWorkspace===undefined')
            page.unroute('**/chat',malicious)
            passed('overview_source_values_remain_literal_text')
            tab('现代文献');literature.locator('[name=term]').fill('failure-fixture')
            def unavailable(route):route.fulfill(status=503,json={'detail':'fixture unavailable'})
            page.route('**/chat',unavailable)
            literature.get_by_role('button',name='检索文献',exact=True).click();idle()
            assert literature.locator('[name=term]').input_value()=='failure-fixture'
            assert '查询未完成' in page.locator('[data-workspace-feedback]').inner_text()
            page.unroute('**/chat',unavailable)
            passed('request_failure_keeps_form_draft_and_reenables_controls')
            page.locator('#clear-chat').click();idle()
            assert snapshot()['messages']==[]
            assert form.locator('[name=herb_name]').input_value()=='' and literature.locator('[name=term]').input_value()==''
            assert '没有选择' in page.locator('[data-workspace-status]').inner_text()
            assert '点击' in overview.inner_text()
            assert not report['page_errors'],report['page_errors']
            passed('clear_session_resets_panels_and_backend_task_without_browser_errors')
            browser.close()
        report['status']='passed'
    except Exception as exc:
        report.update(status='failed',failure_type=type(exc).__name__);raise
    finally:
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:7873');parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--browser',type=Path,required=True);args=parser.parse_args()
    print(json.dumps(verify(args.url,args.out.resolve(),args.browser),ensure_ascii=False,indent=2))
