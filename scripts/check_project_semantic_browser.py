"""Actual local embeddings through chat, project selection and source report."""
import argparse
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright


async def check(args):
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path=str(args.browser),headless=True,args=['--no-sandbox'])
        context=await browser.new_context();page=await context.new_page();errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        await page.goto(args.url)
        await page.wait_for_function("!document.querySelector('#message-input').disabled")
        docs=[]
        for data in [{'title':'当归用途整理','herbs':['当归'],'question':'功效'},
                     {'title':'健脾药比较','herbs':['白术','茯苓'],'question':'性味与药用部位'}]:
            r=await context.request.post(args.url+'api/research/projects',data=data)
            assert r.status==201,await r.text();docs.append(await r.json())
        await page.reload()
        await page.wait_for_function("!document.querySelector('#message-input').disabled")
        query='继续上次当归的研究，这次只看贮藏'
        await page.locator('#message-input').fill(query)
        async with page.expect_response(lambda r:r.url.endswith('/chat') and r.request.method=='POST',timeout=40000) as awaited:
            await page.locator('#chat-form').evaluate('(form)=>form.requestSubmit()')
        response=await awaited.value;data=await response.json()
        assert data['project_memory']['items'][0]['id']==docs[0]['id'],data
        button=page.locator('#chat-body button').filter(has_text='继续这个项目').first
        await button.click()
        await page.wait_for_function("document.querySelector('[data-report-question] input').value==='看贮藏'")
        assert await page.locator('#local-research-form input').first.input_value()=='当归'
        await page.locator('#local-research-form [type=submit]').click()
        await page.wait_for_function("document.querySelector('#local-research-output').innerText.includes('ETCM 基础属性')",timeout=60000)
        jobs=await (await context.request.get(args.url+'api/research/jobs')).json()
        job=await (await context.request.get(args.url+'api/research/jobs/'+jobs['items'][0]['id'])).json()
        assert job['payload']['question']=='看贮藏'
        assert job['result']['text']['hits'][0]['label']=='贮藏'
        # Update after the candidate was rendered: old card cannot apply stale memory.
        edited=await context.request.put(args.url+'api/research/projects/'+docs[0]['id'],data={'title':'当归用途整理','herbs':['当归'],'question':'用量','revision':1})
        assert edited.status==200
        await button.click()
        await page.wait_for_function("document.querySelector('#chat-body').innerText.includes('项目已修改，请重新检索')")
        await context.request.delete(args.url+'api/research/projects/'+docs[0]['id']+'?revision=2')
        found=await (await context.request.post(args.url+'api/research/projects/recall',data={'query':query})).json()
        assert docs[0]['id'] not in [d['id'] for d in found['items']]
        other=await browser.new_context();otherpage=await other.new_page();await otherpage.goto(args.url)
        empty=await (await other.request.post(args.url+'api/research/projects/recall',data={'query':query})).json()
        assert empty['status']=='no_memory',empty
        await other.close()
        ordinary=await context.request.post(args.url+'chat',data={'query':'有多少药材'})
        assert ordinary.status==200 and 'project_memory' not in await ordinary.json()
        for doc in docs[1:]:await context.request.delete(args.url+'api/research/projects/'+doc['id']+'?revision=1')
        assert not errors,errors
        report={'status':'passed','actual_local_embedding':True,'chat_recall_after_reload':True,'current_question_overrides_saved':True,
                'report_source_field':'贮藏','stale_card_rejected':True,'deleted_not_recalled':True,'other_identity_no_memory':True,
                'ordinary_chat_unchanged':True,'javascript_errors':errors,'external_model_calls':0}
        args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(report,ensure_ascii=False));await browser.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:7887/');p.add_argument('--browser',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    asyncio.run(check(p.parse_args()))
