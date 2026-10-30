"""Real browser: concise cross-source table, source folds, evidence read and partials."""
import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.async_api import async_playwright
QUESTION='比较Alpha与Beta的药典功效和用量，核对全部共同靶点，列出双方的ETCM药用部位，保留双方原始记录的位置'


async def check(args):
    errors=[];blocked=[];origin=urlsplit(args.url).netloc
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path=str(args.browser),headless=True,args=['--no-sandbox'])
        context=await browser.new_context();page=await context.new_page()
        async def route(r):
            if urlsplit(r.request.url).netloc==origin:await r.continue_()
            else:blocked.append(r.request.url);await r.abort()
        await context.route('**/*',route);page.on('pageerror',lambda e:errors.append(str(e)))
        await page.goto(args.url)
        async def ask(question):
            await page.locator('#message-input').fill(question)
            async with page.expect_response(lambda r:r.url.endswith('/chat') and r.request.method=='POST',timeout=45000) as response:
                await page.locator('#chat-form').evaluate('(f)=>f.requestSubmit()')
            return (await (await response.value).json())['assistant']
        result=await ask(QUESTION);assert result['status']=='ok',result
        assert result['execution']['tool_calls']==6 and result['execution']['action_calls']==1
        card=page.locator('[data-research-plan]').last;await card.wait_for()
        assert await card.locator('details[open]').count()==0
        section=card.locator('[data-analysis-block="pair_evidence_report"]')
        assert await section.locator('table tbody tr').count()==3
        assert 'etcm2-local:' not in await card.inner_text()
        assert 'SYNTHETIC_Alpha' in await section.inner_text()
        first=section.locator('table tbody tr').first
        await first.locator('details > summary').first.click()
        assert await first.locator('pre:visible').count()==1
        reads=section.locator('[data-evidence-reads]');await reads.locator('summary').first.click()
        target=reads.locator(':scope > div').filter(has_text='共同靶点原始记录').first
        async def read_page(button):
            async with page.expect_response(lambda r:'/evidence/obs-1?' in r.url) as response:
                await button.click()
            value=await (await response.value).json()
            assert value['status']=='ok',value
            last=value['data']['items'][-1]['identifier']
            await target.locator('pre').filter(has_text=last).wait_for()
            return value['data']
        part=await read_page(target.get_by_role('button',name='读取前20项'))
        identifiers=[x['identifier'] for x in part['items']];read_pages=1
        while part['next_offset'] is not None:
            part=await read_page(target.get_by_role('button',name='下一页',exact=True))
            identifiers.extend(x['identifier'] for x in part['items']);read_pages+=1
            assert read_pages<=23
        assert identifiers==['P'+str(i).zfill(5) for i in range(23)]
        async with page.expect_download() as download:
            await reads.get_by_role('link',name='下载这份完整证据 JSON').click()
        saved=args.out.with_suffix('.download.json');await (await download.value).save_as(saved)
        full=json.loads(saved.read_text())['data']['report'];assert len(full['targets']['items'])==23
        await page.screenshot(path=str(args.out.with_suffix('.png')),full_page=True)
        await page.reload();await page.locator('[data-research-plan]').wait_for()
        restored=(await (await context.request.get(args.url+'conversation')).json())['assistant']
        assert restored['run_id']==result['run_id']
        partial=await ask(QUESTION.replace('功效和用量','功效和用量和贮藏'))
        assert partial['status']=='partial' and partial['blocks'][0]['missing']==['Alpha：贮藏','Beta：贮藏']
        assert '缺项' in await page.locator('[data-research-plan]').last.inner_text()
        assert not errors,errors
        report={'status':'passed','data':'synthetic_only','external_model_calls':0,'field_rows':3,
            'source_reads':6,'outer_actions':1,'shared_targets':23,'source_folds':True,
            'paged_read_and_full_download':True,'read_pages':read_pages,'refresh_same_run':True,'partial_fields_explicit':True,
            'javascript_errors':errors,'blocked_external_assets':len(blocked),'font_layout_reviewed':False}
        args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report));await browser.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--url',required=True)
    p.add_argument('--browser',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();args.out.parent.mkdir(parents=True,exist_ok=True);asyncio.run(check(args))
