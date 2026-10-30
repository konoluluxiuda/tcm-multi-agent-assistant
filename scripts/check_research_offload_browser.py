"""Read, page, download and reopen externalized evidence through the real chat UI."""
import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.async_api import async_playwright

QUESTION='查找Synthetic disease的来源，读取靶点资料，再按Jaccard列出前五个药材候选，保留出处'


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
        await page.locator('#message-input').fill(QUESTION)
        async with page.expect_response(lambda r:r.url.endswith('/chat') and r.request.method=='POST',timeout=45000) as response:
            await page.locator('#chat-form').evaluate('(f)=>f.requestSubmit()')
        result=(await (await response.value).json())['assistant'];assert result['status']=='ok',result
        card=page.locator('[data-research-plan]').last;await card.wait_for()
        assert await card.locator('[data-evidence-reads]').count()==2
        assert await card.locator('details[open]').count()==0
        profile=card.locator('[data-analysis-block="disease_gene_profile"]')
        reads=profile.locator('[data-evidence-reads]');await reads.locator('summary').first.click()
        first=reads.locator(':scope > div').first
        await first.get_by_role('button',name='读取前20项').click()
        await first.locator('pre').filter(has_text='P00019').wait_for()
        await first.get_by_role('button',name='下一页',exact=True).click()
        await first.locator('pre').filter(has_text='P00039').wait_for()
        await first.get_by_role('button',name='上一页',exact=True).click()
        await first.locator('pre').filter(has_text='P00000').wait_for()
        async with page.expect_download() as download:
            await reads.get_by_role('link',name='下载这份完整证据 JSON').click()
        saved=args.out.with_suffix('.download.json');await (await download.value).save_as(saved)
        payload=json.loads(saved.read_text());assert len(payload['data']['target_ids'])==14000
        assert payload['data']['target_ids'][7000]=='P07000'
        await page.screenshot(path=str(args.out.with_suffix('.png')),full_page=True)
        await page.reload();await page.locator('[data-research-plan]').wait_for()
        restored=(await (await context.request.get(args.url+'conversation')).json())['assistant']
        assert restored['run_id']==result['run_id'];assert restored['execution']==result['execution']
        fallback=await page.evaluate('''()=>{const c=document.createElement('div');
            renderResearchPlan(c,{run_id:'synthetic',blocks:[{tool:'herb_target_profile',title:'Synthetic preview',
            target_count:14000,externalized:true,preview_omitted:true,evidence_ids:['obs-1'],
            evidence_reads:[{evidence_id:'obs-1',field:'target_ids',count:14000}]}]},()=>{});
            return c.textContent}''')
        assert '当前无可用标识' not in fallback and '按需读取或下载' in fallback
        assert not errors,errors
        report={'status':'passed','data':'synthetic_only','external_model_calls':0,
            'profile_targets':14000,'externalized_blocks':2,'read_next_previous':True,
            'download_preserves_full_payload':True,'refresh_restores_same_run':True,
            'initially_folded':True,'javascript_errors':errors,'blocked_external_assets':len(blocked),
            'omitted_preview_not_claimed_as_empty':True,
            'font_layout_reviewed':False,'run_id':result['run_id']}
        args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report))
        await browser.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--url',required=True)
    p.add_argument('--browser',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();args.out.parent.mkdir(parents=True,exist_ok=True);asyncio.run(check(args))
