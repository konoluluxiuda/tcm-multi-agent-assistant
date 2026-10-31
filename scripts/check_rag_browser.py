"""Local browser acceptance. Only original/blocked tasks; zero provider calls."""
import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright


async def check(args):
    origin=urlsplit(args.url)
    if origin.scheme!='http' or origin.hostname!='127.0.0.1':raise ValueError('Local HTTP only')
    if args.out.exists():raise FileExistsError('Preserve original browser reports')
    errors=[];external=[]
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(executable_path=str(args.browser),headless=True,args=['--no-sandbox'])
        ctx=await browser.new_context()
        async def restrict(route):
            if urlsplit(route.request.url).netloc!=origin.netloc:
                external.append(route.request.url);await route.abort()
            else:await route.continue_()
        await ctx.route('**/*',restrict)
        page=await ctx.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        await page.goto(args.url+'/')
        assert await page.locator('a[href="/rag"]').count()==1
        await page.locator('a[href="/rag"]').click()
        await page.wait_for_function('!document.querySelector("#mode").textContent.includes("正在")')
        capability=await (await ctx.request.get(args.url+'/api/rag/capabilities')).json()
        runtime=await (await ctx.request.get(args.url+'/api/runtime')).json()
        assert not runtime['restart_required']
        assert capability['generation_enabled']==args.expect_generation
        assert await page.locator('#compose').is_disabled() is (not args.expect_generation)
        async with page.expect_response(lambda r:r.url.endswith('/api/rag/answers') and r.request.method=='POST') as response:
            await page.locator('#original').click()
        response=await response.value;data=await response.json();body=response.request.post_data_json
        assert response.status==200 and data['status']=='original' and data['stats']['requests']==0
        assert len(data['bundle']['sources'])==2
        await page.locator('#sources details').first.wait_for();assert await page.locator('#sources details').count()==2
        await page.locator('#sources summary').first.click()
        await page.screenshot(path=str(args.out.with_suffix('.png')),full_page=True)
        replay=await ctx.request.post(args.url+'/api/rag/answers',data=body)
        assert replay.status==200 and (await replay.json())['replayed']
        if args.expect_generation:
            for question,status in [('只查白术独立的【注意】字段','missing_field'),
                    ('我记录的是半夏，也可能是姜半夏或法半夏，炮制形式还没确定。请先核对条目。','clarification_required')]:
                await page.locator('#question').fill(question)
                await page.locator('#compose').wait_for(state='visible')
                await page.wait_for_function('!document.querySelector("#compose").disabled')
                async with page.expect_response(lambda r:r.url.endswith('/api/rag/answers')) as response:
                    await page.locator('#compose').click()
                result=await (await response.value).json()
                assert result['status']==status and result['stats']['requests']==0
        await page.reload()
        assert not errors and not external,(errors,external)
        report={'status':'passed','port':origin.port,'runtime_fresh':True,
                'generation_configured':args.expect_generation,'original_answer':True,'source_cards':2,
                'same_key_replayed':True,'blocked_requests_no_generation':args.expect_generation,
                'javascript_errors':errors,'external_browser_requests':external,'provider_requests':0}
        args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(report,ensure_ascii=False));await browser.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url',required=True);p.add_argument('--browser',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--expect-generation',action='store_true')
    asyncio.run(check(p.parse_args()))
