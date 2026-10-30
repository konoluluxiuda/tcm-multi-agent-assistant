"""Real local UI checks for natural field/pair requests; providers stay disabled."""
import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.async_api import async_playwright


async def check(args):
    parsed=urlsplit(args.url)
    if parsed.scheme!='http' or parsed.hostname!='127.0.0.1':raise ValueError('Local origin required')
    errors=[];blocked=[];cases=[]
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(executable_path=str(args.browser),headless=True,args=['--no-sandbox'])
        for question,tool,rows in [
            ('查看白术的药用部位和科名','herb_attributes',2),
            ('我正在整理白术与黄芪的资料，请分别对照药典中的功效、用量，然后给出两者共同的靶点和原始出处','pair_evidence_report',2),
            ('帮我把黄芪的药典功效和白术在ETCM的药用部位整理到一起，附各自出处','pharmacopoeia_lookup',1)]:
            context=await browser.new_context()
            async def local(route):
                if urlsplit(route.request.url).netloc==parsed.netloc:await route.continue_()
                else:blocked.append(route.request.resource_type);await route.abort()
            await context.route('**/*',local)
            page=await context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
            await page.goto(args.url)
            await page.locator('#message-input').fill(question)
            async with page.expect_response(lambda r:r.url.endswith('/chat') and r.request.method=='POST') as response:
                await page.locator('#chat-form').evaluate('(f)=>f.requestSubmit()')
            body=await (await response.value).json();assistant=body['assistant']
            assert assistant['status']=='queued',body
            card=page.locator('[data-research-plan]').last
            await card.locator('[data-analysis-block="'+tool+'"]').wait_for(timeout=60000)
            assert await card.locator('[data-analysis-block="'+tool+'"] table tbody tr').count()==rows
            assert await card.locator('details[open]').count()==0
            visible=await card.inner_text()
            assert '核对请求清单' not in visible and 'etcm2-local:' not in visible
            record=(await (await context.request.get(args.url+'conversation')).json())['assistant']
            assert record['status']=='ok' and record['run_id']==assistant['job_id'],record
            # The source evidence remains available even though the main answer
            # hides file hashes and raw JSON until explicitly requested.
            _,first=record['blocks'][0]['evidence_ids'][0].split('-',1)
            evidence=await context.request.get(args.url+'api/research/runs/'+record['run_id']+'/evidence/obs-'+first+'/download')
            assert evidence.status==200
            source=card.locator('details').first;await source.locator('summary').first.click()
            assert await source.get_attribute('open') is not None
            await page.reload()
            restored=page.locator('[data-research-plan]').last
            await restored.locator('[data-analysis-block="'+tool+'"]').wait_for()
            assert await restored.locator('details[open]').count()==0
            again=(await (await context.request.get(args.url+'conversation')).json())['assistant']
            assert again['run_id']==record['run_id']
            cases.append({'tool':tool,'field_rows':rows,'same_run_after_refresh':True,'evidence_download':True,'source_fold_interactive':True})
            await context.close()
        await browser.close()
    assert not errors,errors
    result={'status':'passed','cases':cases,'javascript_errors':errors,'blocked_external_assets':len(blocked),
            'external_model_calls':0,'font_layout_reviewed':False}
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--url',required=True)
    parser.add_argument('--browser',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    asyncio.run(check(parser.parse_args()))
