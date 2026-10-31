"""Real local corpus browser acceptance; requires explicitly synthetic driver."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
import zipfile
from playwright.async_api import async_playwright


async def check(args):
    origin=urlsplit(args.url)
    if origin.scheme!='http' or origin.hostname!='127.0.0.1':raise ValueError('Local preview only')
    args.out.mkdir(parents=True,exist_ok=False);errors=[];external=[]
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(executable_path=str(args.browser),headless=True,args=['--no-sandbox'])
        ctx=await browser.new_context(accept_downloads=True)
        async def restrict(route):
            if urlsplit(route.request.url).netloc!=origin.netloc:
                external.append(route.request.url);await route.abort()
            else:await route.continue_()
        await ctx.route('**/*',restrict)
        page=await ctx.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        await page.goto(args.url+'/dataops/research')
        await page.wait_for_function('!document.querySelector("#mode").textContent.includes("正在")')
        capability=await (await ctx.request.get(args.url+'/api/dataops/capabilities')).json()
        assert capability['research_selection']['driver']=='synthetic'
        assert not capability['research_selection']['external_model_enabled']
        runtime=await (await ctx.request.get(args.url+'/api/runtime')).json();assert not runtime['restart_required']
        await page.locator('#names').fill('白术，黄芪，川芎，甘草，大黄')
        await page.locator('#fields input[value="注意"]').check()
        async with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/api/dataops/research-tasks')) as submitted:
            await page.locator('#submit').click()
        response=await submitted.value;assert response.status==202;body=response.request.post_data_json
        await page.wait_for_function('current && current.job.status==="complete"',timeout=120000)
        first=await page.evaluate('current.workspace')
        assert first['result']['research']['qualified_count']==2
        assert {r['name'] for r in first['result']['rows']}=={'GanCao','DaHuang'}
        async with page.expect_download() as download:await page.locator('#output > a').click()
        file=args.out/'v1.xlsx';await (await download.value).save_as(str(file))
        assert hashlib.sha256(file.read_bytes()).hexdigest()==first['artifact']['sha256'] and zipfile.is_zipfile(file)
        (args.out/'v1.private.json').write_text(json.dumps(first,ensure_ascii=False,indent=2))
        replay=await ctx.request.post(args.url+'/api/dataops/research-tasks',data=body)
        assert replay.status==202 and (await replay.json())['workspace']['id']==first['id']
        await page.locator('#fields input[value="贮藏"]').check()
        await page.locator('#revision').click()
        await page.wait_for_function('current && current.workspace.revision===2 && current.job.status==="complete"',timeout=120000)
        second=await page.evaluate('current.workspace')
        assert second['parent_id']==first['id'] and second['result']['research']['selected_ids']==first['result']['research']['selected_ids']
        assert second['goal']['document_fields']==['功能与主治','注意','贮藏']
        async with page.expect_download() as download:await page.locator('#output > a').click()
        file2=args.out/'v2.xlsx';await (await download.value).save_as(str(file2))
        (args.out/'v2.private.json').write_text(json.dumps(second,ensure_ascii=False,indent=2))
        assert hashlib.sha256(file.read_bytes()).hexdigest()==first['artifact']['sha256']
        assert hashlib.sha256(file2.read_bytes()).hexdigest()==second['artifact']['sha256']
        await page.reload();await page.wait_for_function('current && current.workspace.revision===2',timeout=30000)
        await page.screenshot(path=str(args.out/'v2.png'),full_page=True)
        await page.locator('#names').fill('白术，黄芪');await page.locator('#fields input[value="贮藏"]').uncheck()
        await page.locator('#fields input[value="注意"]').check()
        await page.locator('#submit').click()
        await page.wait_for_function('current && current.workspace.revision===1 && current.job.status==="partial"',timeout=120000)
        partial=await page.evaluate('current.workspace');assert partial['result']['research']['qualified_count']==0 and partial['artifact']
        assert not errors and not external,(errors,external)
        report={'status':'passed','port':origin.port,'fresh_runtime':True,'selection_correct':True,
                'v2_preserves_parent':True,'v1_file_unchanged':True,'same_key_replayed':True,'refresh_restores_task':True,
                'insufficient_candidates_explicit_partial':True,'downloads_sha256_verified':True,'provider_requests':0,
                'javascript_errors':errors,'external_browser_requests':external}
        (args.out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(report,ensure_ascii=False));await browser.close()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--url',required=True)
    p.add_argument('--browser',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    asyncio.run(check(p.parse_args()))
