"""Real Chromium, in-process ASGI, synthetic sources, no model/network service."""
import json
import os
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import dotenv


def main():
    from playwright.sync_api import sync_playwright
    from tests import test_requirements_v3_http as fixture
    from tests.test_answer_delivery import requirements
    case=fixture.RequirementsV3HTTPTests('test_workbench_executes_saved_query_proposal')
    case.setUp()
    try:
        index,pdf=case._pharma_fixture()
        herbs=Path(os.environ['DATAOPS_ROOT']).parent/'sources/herbs.sqlite3'
        draft=requirements();draft['objects']=draft['objects'][:1]
        draft['requirements'][0]['object_refs']=['o1']
        draft['requirements'][0]['fields'].append({'namespace':'pharmacopoeia','name':'功能与主治'})
        env={'TCM_DB':str(herbs),'TCM_DATASET_VERSION':'fixture-v1',
             'PHARMACOPOEIA_INDEX':str(index),'PHARMACOPOEIA_PDF_HIGH':str(pdf),
             'PHARMACOPOEIA_PDF_FULL':str(pdf),'ENABLE_PHARMACOPOEIA_RESEARCH':'1'}
        models=[]
        def model(**kw):
            m=fixture.RequirementsSequence(draft,draft);models.append(m);return m
        with patch.dict(os.environ,env),patch('tcm.dataops.research_model.configured_model',side_effect=model), \
             patch('tcm.dataops.request_policy.needs_fallback',return_value=True),sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/tmp/tcm-playwright-20261012/chromium-1243/chrome-linux64/chrome',
                                      headless=True,args=['--no-sandbox'])
            context=browser.new_context();page=context.new_page();errors=[];external=[]
            page.on('pageerror',lambda e:(errors.append(str(e)),print('JS ERROR',str(e),flush=True)))
            def serve(route):
                request=route.request;url=urlsplit(request.url)
                if url.hostname!='testserver':external.append(request.url);route.abort();return
                response=case.client.request(request.method,request.url,
                    content=request.post_data_buffer,headers={k:v for k,v in request.headers.items()
                                                             if k.lower() not in ('host','content-length','cookie')})
                headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-encoding','transfer-encoding','content-length')}

                if request.method=='POST':print('POST',url.path,response.status_code,response.text[:700],flush=True)
                route.fulfill(status=response.status_code,headers=headers,body=response.content)
            context.route('**/*',serve)
            question='Alpha的药用部位和功效'
            page.goto('https://testserver/')
            page.locator('#message-input').fill(question)
            page.locator('#chat-form button[type=submit]').click()
            try:
                page.wait_for_function("document.querySelectorAll('[data-answer-step]').length===2")
            except Exception:
                print('DOM',page.locator('body').inner_text()[-5000:],flush=True)
                raise
            assert 'Leaf' in page.locator('[data-answer-step=s1]').inner_text()
            assert 'TEST_EFFICACY' in page.locator('[data-answer-step=s2]').inner_text()
            page.locator('[data-answer-step=s2] summary').filter(has_text='出处与校订信息').click()
            page.locator('[data-answer-step=s2] summary').filter(has_text='字段原页').click()
            assert '物理第 1 页' in page.locator('[data-answer-step=s2]').inner_text()
            page.locator('[data-answer-step=s2] button').filter(has_text='查看原页').first.click()
            page.locator('[data-answer-step=s2] img').first.wait_for()
            assert page.locator('[data-answer-step=s2] img').first.evaluate('(img) => img.complete && img.naturalWidth > 0')
            page.reload();page.wait_for_function("document.querySelectorAll('[data-answer-step]').length===2")
            assert 'TEST_EFFICACY' in page.locator('[data-answer-step=s2]').inner_text()
            out=Path('var/answer_delivery_v2/browser');out.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(out/'chat.png'),full_page=True)
            page.goto('https://testserver/dataops/research')
            page.locator('#question_model').uncheck()
            page.locator('#question').fill(question);page.locator('#question_submit').click()
            page.wait_for_function("document.querySelector('#message').textContent.includes('需要补充')")
            assert page.locator('#question_submit').is_enabled()
            assert page.locator('#submit').is_enabled()
            page.locator('#question_model').check();page.locator('#question_submit').click()
            page.wait_for_function("document.querySelectorAll('[data-answer-step]').length===2")
            assert 'TEST_EFFICACY' in page.locator('#output').inner_text()
            before=sum(m.calls for m in models)
            page.locator('#question_submit').click()
            page.wait_for_function("document.querySelector('#question_hint').textContent.includes('未重复执行')")
            assert sum(m.calls for m in models)==before
            page.reload();page.wait_for_function("document.querySelectorAll('[data-answer-step]').length===2")
            assert 'Leaf' in page.locator('#output').inner_text()
            assert 'TEST_EFFICACY' in page.locator('#output').inner_text()
            page.screenshot(path=str(out/'workbench.png'),full_page=True)
            # A partial reply visibly retains successful text and the unfinished list.
            from tests.test_answer_delivery import output
            page.locator('#question').fill(question+'，保留出处')
            with patch('agents.agent_decision.process_query',side_effect=[output(label='KEPT_RESULT'),RuntimeError('fixture')]):
                page.locator('#question_submit').click()
                page.wait_for_function("document.querySelector('#output [data-answer-step]') && document.querySelector('#message').textContent.includes('部分')")
            assert 'KEPT_RESULT' in page.locator('#output').inner_text()
            assert '未完成项' in page.locator('#output').inner_text()
            assert not errors,errors
            assert not external,external
            report={'status':'passed','checks':['chat_cross_source_visible','citation_details_visible','original_pdf_page_rendered',
                'chat_refresh_restores_all','workbench_clarification_reenables_submit',
                'workbench_query_after_clarification','workbench_replay_no_new_understanding',
                'workbench_refresh_restores_all','partial_keeps_success_and_unfinished'],
                'javascript_errors':errors,'external_browser_requests':external,
                'real_model_requests':0,'transport':'Playwright route -> real ASGI TestClient; synthetic data/SDK replies'}
            (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
            print(json.dumps(report,ensure_ascii=False));browser.close()
    finally:case.doCleanups()


if __name__=='__main__':
    with patch.object(dotenv,'load_dotenv',return_value=False):main()
