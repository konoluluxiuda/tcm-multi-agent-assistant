"""Check an optional local-reranker preview through the real chat/report page."""
import argparse
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright


async def check(args):
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path=str(args.browser),headless=True,args=['--no-sandbox'])
        context=await browser.new_context();page=await context.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        await page.goto(args.url)
        await page.locator('#local-research-panel > summary').click()
        await page.locator('[data-research-mode]').select_option('report')
        await page.locator('#local-research-form input').first.fill('当归')
        await page.locator('[data-report-question] input').fill('功效和贮藏，重点是贮藏')
        await page.locator('#local-research-form [type=submit]').click()
        await page.wait_for_function("document.querySelector('#local-research-output').innerText.includes('ETCM 基础属性')",timeout=45000)
        history=await (await context.request.get(args.url.rstrip('/')+'/api/research/jobs')).json()
        identifier=history['items'][0]['id']
        saved=await (await context.request.get(args.url.rstrip('/')+'/api/research/jobs/'+identifier)).json()
        text=saved['result']['text'];metadata=text['retrieval']
        assert metadata['reranker']['status']=='used',metadata
        assert metadata['backend']=='qdrant_local' and metadata['dense']=='used'
        assert all('rerank_score' in hit and hit['evidence'] for hit in text['hits'])
        await page.reload();await page.locator('#local-research-panel > summary').click()
        await page.locator('#local-research-panel details > summary').click()
        await page.locator('#local-research-history button').filter(has_text='回看').first.click()
        await page.wait_for_function("document.querySelector('#local-research-output').innerText.includes('ETCM 基础属性')")
        assert not errors,errors
        args.out.mkdir(parents=True,exist_ok=True)
        (args.out/'summary.json').write_text(json.dumps({'status':'passed','model_used':metadata['reranker']['model'],
            'candidate_count':metadata['reranker']['candidates'],'reranker_used':True,'qdrant_used':True,
            'history_reloaded':True,'javascript_errors':errors},ensure_ascii=False,indent=2))
        await page.screenshot(path=str(args.out/'browser.png'),full_page=True)
        await browser.close()
        print('Qdrant + reranker browser passed')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--url',default='http://127.0.0.1:7881/')
    p.add_argument('--browser',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    asyncio.run(check(p.parse_args()))
