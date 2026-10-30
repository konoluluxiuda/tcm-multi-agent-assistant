"""Browser acceptance: async chat, page closure, SSE replay, evidence and cancel."""
import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.async_api import async_playwright

QUESTION='比较Alpha与Beta的药典功效和用量，核对全部共同靶点，列出双方的ETCM药用部位，保留双方原始记录的位置'


async def check(args):
    origin=urlsplit(args.url).netloc;errors=[];blocked=[]
    async with async_playwright() as playwright:
        browser=await playwright.chromium.launch(executable_path=str(args.browser),headless=True,args=['--no-sandbox'])
        context=await browser.new_context()
        async def route(request):
            if urlsplit(request.request.url).netloc==origin:await request.continue_()
            else:blocked.append(request.request.url);await request.abort()
        await context.route('**/*',route)
        page=await context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));await page.goto(args.url)
        initial_reads=(await (await context.request.get(args.url+'__test__/plan-stats')).json())['source_reads']
        async def ask(page):
            await page.locator('#message-input').fill(QUESTION)
            async with page.expect_response(lambda r:r.url.endswith('/chat') and r.request.method=='POST') as reply:
                await page.locator('#chat-form').evaluate('(f)=>f.requestSubmit()')
            result=(await (await reply.value).json())['assistant'];assert result['status']=='queued',result;return result
        first=await ask(page);identifier=first['job_id']
        # Close the entire page while the synthetic worker is waiting in a source read.
        await page.close()
        for _ in range(250):
            doc=await (await context.request.get(args.url+'api/research/jobs/'+identifier)).json()
            if doc['status']=='complete':break
            assert doc['status'] in ('queued','running'),doc
            await asyncio.sleep(.05)
        else:raise TimeoutError('Detached task did not complete')
        assert doc['result']['run']['tool_calls']==6
        reads_before=(await (await context.request.get(args.url+'__test__/plan-stats')).json())['source_reads']
        assert reads_before-initial_reads==6
        replay=await context.request.get(args.url+'api/research/jobs/'+identifier+'/events',headers={'Last-Event-ID':str(doc['revision'])})
        assert 'event: done' in await replay.text() and 'event: progress' not in await replay.text()
        page=await context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));await page.goto(args.url)
        card=page.locator('[data-research-plan]').last
        await card.locator('[data-analysis-block="pair_evidence_report"]').wait_for()
        assert await card.locator('table tbody tr').count()==3
        assert await card.locator('details[open]').count()==0
        assert 'etcm2-local:' not in await card.inner_text()
        restored=(await (await context.request.get(args.url+'conversation')).json())['assistant']
        assert restored['run_id']==identifier
        download=await context.request.get(args.url+'api/research/runs/'+identifier+'/evidence/obs-1/download')
        assert download.status==200 and len((await download.json())['data']['report']['targets']['items'])==23
        assert (await (await context.request.get(args.url+'__test__/plan-stats')).json())['source_reads']==reads_before
        await page.screenshot(path=str(args.out.with_suffix('.png')),full_page=True)
        second=await ask(page);cancel=page.locator('[data-plan-job="'+second['job_id']+'"]').get_by_role('button',name='取消任务')
        await cancel.click()
        for _ in range(100):
            state=await (await context.request.get(args.url+'api/research/jobs/'+second['job_id'])).json()
            if state['status']=='cancelled':break
            await asyncio.sleep(.05)
        else:raise TimeoutError('Cancel did not stop task')
        await page.reload();restored=(await (await context.request.get(args.url+'conversation')).json())['assistant']
        assert restored['status']=='cancelled' and restored['job_id']==second['job_id']
        control=await context.request.post(args.url+'api/research/jobs/__test__/pause-saved');assert control.status==200
        third=await ask(page)
        for _ in range(250):
            stats=await (await context.request.get(args.url+'__test__/plan-stats')).json()
            if stats['saved_ready']:break
            await asyncio.sleep(.05)
        else:raise TimeoutError('Saved boundary not reached')
        before_resume=stats['source_reads'];control=await context.request.post(args.url+'api/research/jobs/__test__/interrupt');assert control.status==200
        await page.reload()
        resume=page.locator('[data-plan-job="'+third['job_id']+'"]').get_by_role('button',name='恢复已保存步骤')
        await resume.click()
        for _ in range(100):
            resumed=await (await context.request.get(args.url+'api/research/jobs/'+third['job_id'])).json()
            if resumed['status']=='complete':break
            await asyncio.sleep(.05)
        else:raise TimeoutError('Web resume did not complete')
        assert resumed['result']['run']['tool_calls']==6
        assert (await (await context.request.get(args.url+'__test__/plan-stats')).json())['source_reads']==before_resume
        other=await browser.new_context();await other.request.get(args.url+'conversation')
        assert (await other.request.get(args.url+'api/research/jobs/'+identifier)).status==404
        assert not errors,errors
        result={'status':'passed','data':'synthetic_only','external_model_calls':0,'page_closed_while_running':True,
            'completed_after_page_closed':True,'same_job_after_refresh':True,'source_reads':6,'field_rows':3,
            'terminal_cursor_replay':True,'evidence_download':True,'cancel_button_and_refresh':True,'web_resume_without_requery':True,
            'cross_session_404':True,'javascript_errors':errors,'blocked_external_assets':len(blocked),'font_layout_reviewed':False}
        args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result));await browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--url',required=True)
    parser.add_argument('--browser',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();args.out.parent.mkdir(parents=True,exist_ok=True);asyncio.run(check(args))
