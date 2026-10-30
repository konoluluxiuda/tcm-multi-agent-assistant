"""Exercise the real chat UI against a synthetic-only local fixture server."""
import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.async_api import async_playwright

QUESTION='查找Synthetic disease的来源，读取全部靶点原始记录，再按Jaccard列出前五个药材候选，保留出处'


async def check(args):
    origin=urlsplit(args.url).netloc;blocked=[];errors=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path=str(args.browser),headless=True,args=['--no-sandbox'])
        context=await browser.new_context();page=await context.new_page()
        async def route(request_route):
            if urlsplit(request_route.request.url).netloc==origin:await request_route.continue_()
            else:blocked.append(request_route.request.url);await request_route.abort()
        await context.route('**/*',route)
        page.on('pageerror',lambda error:errors.append(str(error)))
        await page.goto(args.url)
        await page.locator('#message-input').fill(QUESTION)
        async with page.expect_response(lambda r:r.url.endswith('/chat') and r.request.method=='POST',timeout=45000) as response:
            await page.locator('#chat-form').evaluate('(form)=>form.requestSubmit()')
        result=await (await response.value).json()
        assert result['assistant']['status']=='ok',result
        card=page.locator('[data-research-plan]').last
        await card.wait_for()
        assert await card.locator('details[open]').count()==0
        assert await card.locator('[data-analysis-block="rank_herbs_for_gene_set"] tbody tr').count()==2
        assert '核对请求清单' not in await card.inner_text()
        assert 'etcm-disease:' not in await card.inner_text()
        assert not await card.locator('pre:visible').count()
        await card.locator('[data-analysis-block="disease_gene_profile"] > details').first.locator('summary').first.click()
        assert await card.locator('[data-analysis-block="disease_gene_profile"] tbody tr').count()==24
        await page.screenshot(path=str(args.out.with_suffix('.png')),full_page=True)
        run_id=result['assistant']['run_id']
        await page.reload()
        await page.locator('[data-research-plan]').wait_for()
        restored=await (await context.request.get(args.url+'conversation')).json()
        assert restored['assistant']['run_id']==run_id
        await page.locator('#message-input').fill(QUESTION.replace('Synthetic disease','Synthetic'))
        async with page.expect_response(lambda r:r.url.endswith('/chat') and r.request.method=='POST',timeout=45000):
            await page.locator('#chat-form').evaluate('(form)=>form.requestSubmit()')
        pending=page.locator('[data-research-plan]').last
        await pending.get_by_role('button',name='选择 Synthetic disease',exact=True).wait_for()
        async with page.expect_response(lambda r:r.url.endswith('/chat') and r.request.method=='POST',timeout=45000) as selected:
            await pending.get_by_role('button',name='选择 Synthetic disease',exact=True).click()
        chosen=await (await selected.value).json()
        assert chosen['assistant']['status']=='ok',chosen
        assert len(chosen['assistant']['blocks'][0]['rows'])==24
        assert not errors,errors
        report={'status':'passed','data':'synthetic_only','external_model_calls':0,
                'chat_form_completed':True,'profile_rows':24,'ranking_candidates':2,
                'provenance_folded_by_default':True,'source_choice_retains_full_question':True,
                'refresh_restores_saved_result':True,'javascript_errors':errors,
                'blocked_external_assets':len(blocked),'visual_font_quality_reviewed':False}
        args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(report));await browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:7901/')
    parser.add_argument('--browser',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();args.out.parent.mkdir(parents=True,exist_ok=True)
    asyncio.run(check(args))
