"""Browser regression: unavailable overview responses must render their structured message."""
import argparse
import json
import os
from pathlib import Path


def verify(url, browser_path):
    from playwright.sync_api import sync_playwright
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=str(browser_path))
        try:
            page=browser.new_page()
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(url+'/') else route.abort())
            page.goto(url)
            page.wait_for_function('()=>!document.getElementById("message-input").disabled')
            for entry in ('chat','overview_button'):
                with page.expect_response(lambda r:r.url==url+'/chat' and r.request.method=='POST') as pending:
                    if entry=='chat':
                        page.locator('#message-input').fill('数据概览')
                        page.locator('#chat-form').evaluate('(form)=>form.requestSubmit()')
                    else:
                        page.get_by_role('tab',name='数据概览',exact=True).click()
                        page.get_by_role('button',name='更新数据概览',exact=True).click()
                response=pending.value
                assert response.status==503, 'Use the synthetic preview with both real sources unavailable'
                payload=response.json()
                assert payload['assistant']['status']=='unavailable'
                assert payload['assistant']['llm_calls']==0
                page.wait_for_function('()=>!document.getElementById("message-input").disabled')
                card=page.locator('[data-assistant-topic="overview"]').last
                assert '当前数据不可用' in card.inner_text()
                assert payload['response']==card.inner_text()
                assert 'Sorry, there was an error processing' not in page.locator('#chat-body').inner_text()
            page.reload()
            page.wait_for_function('()=>!document.getElementById("message-input").disabled')
            assert '当前本地资料概览' in page.locator('#chat-body').inner_text()
            assert not errors, errors
        finally:
            browser.close()
    return {'status':'passed','checks':['chat_renders_503_overview','overview_button_renders_503_overview',
                                      'saved_explanation_restored_after_reload','no_page_errors'],
            'model_calls':0,'page_errors':errors}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:7875')
    parser.add_argument('--browser',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    args.out.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(args.out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as output:
        report=verify(args.url.rstrip('/'),args.browser)
        json.dump(report,output,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    main()
