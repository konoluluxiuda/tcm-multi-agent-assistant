"""Local question -> goal -> deterministic execution -> immutable XLSX acceptance."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
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
        await page.wait_for_function('!document.querySelector("#question_submit").disabled')
        caps=(await (await ctx.request.get(args.url+'/api/dataops/capabilities')).json())['research_selection']
        assert caps['driver'] in ('fixed','synthetic') and not caps['execution_model_enabled']
        assert not await page.locator('#question_model').is_checked()
        assert not (await (await ctx.request.get(args.url+'/api/runtime')).json())['restart_required']

        async def submit(question):
            await page.locator('#question').fill(question)
            async with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/api/dataops/research-questions')) as response:
                await page.locator('#question_submit').click()
            r=await response.value
            return r,await r.json()

        async def finish(workspace_id):
            await page.wait_for_function('(id)=>current?.workspace.id===id && current.job.status==="complete"',arg=workspace_id,timeout=120000)
            w=await page.evaluate('current.workspace')
            assert w['goal_understanding']['provider_requests']==0
            assert w['result']['research']['provider_requests']==0
            return w

        async def download(w,label):
            async with page.expect_download() as event:await page.locator('#output > a').click()
            target=args.out/(label+'.xlsx');await (await event.value).save_as(str(target))
            assert hashlib.sha256(target.read_bytes()).hexdigest()==w['artifact']['sha256']
            (args.out/(label+'.private.json')).write_text(json.dumps(w,ensure_ascii=False,indent=2))
            return target

        r,v=await submit('整理白术和黄芪的功效、注意事项，保留出处并生成表格')
        assert r.status==202,v
        first=await finish(v['workspace']['id']);first_file=await download(first,'v1')
        assert first['goal']['research']['mode']=='audit_named' and first['goal']['attributes']==['medicinal_part']
        assert set(first['goal']['document_fields'])=={'功能与主治','注意'}
        assert len(first['result']['rows'])==2 and first['result']['research']['qualified_count']==0
        assert all(row['cells']['注意']['status']=='missing' for row in first['result']['rows'])
        replay=await ctx.request.post(args.url+'/api/dataops/research-questions',data=r.request.post_data_json)
        assert replay.status==202 and (await replay.json())['workspace']['id']==first['id']
        r,v=await submit('增加贮藏，保留旧字段');assert r.status==202,v
        second=await finish(v['workspace']['id']);await download(second,'v2')
        assert second['revision']==2 and second['parent_id']==first['id']
        assert second['goal']['document_fields']==first['goal']['document_fields']+['贮藏']
        assert second['goal']['query']==first['goal']['query']
        assert second['result']['research']['selected_ids']==first['result']['research']['selected_ids']
        assert hashlib.sha256(first_file.read_bytes()).hexdigest()==first['artifact']['sha256']
        await page.reload();await page.wait_for_function('(id)=>current?.workspace.id===id',arg=second['id'])
        await page.screenshot(path=str(args.out/'question-v2.png'),full_page=True)

        r,v=await submit('从白术、黄芪、川芎、甘草、大黄中挑两条功效和注意字段齐全的，保留出处并生成表格')
        assert r.status==202,v
        selected=await finish(v['workspace']['id']);await download(selected,'selection')
        assert selected['goal']['query']['limit']==20 and selected['goal']['research']['target_count']==2
        assert selected['goal']['research']['mode']=='select_complete'
        assert {row['name'] for row in selected['result']['rows']}=={'GanCao','DaHuang'}
        question='白术的来源做成表'
        r,v=await submit(question)
        assert r.status==200 and v['proposal']['status']=='needs_input' and 'workspace' not in v,v
        assert await page.locator('#question').input_value()==question
        assert await page.evaluate('current.workspace.id')==selected['id']
        assert not errors and not external,(errors,external)
        report={'status':'passed','port':origin.port,'driver':caps['driver'],
                'optional_question_model_offered':caps['question_model_enabled'],'question_to_goal_to_artifact':True,
                'named_rows_with_missing_retained':True,'selection_distinct_from_named_audit':True,
                'parent_scope_preserved':True,'v1_file_unchanged':True,'same_key_replayed':True,
                'refresh_restores_result':True,'ambiguous_question_creates_no_task':True,
                'downloads_sha256_verified':True,'provider_requests':0,
                'javascript_errors':errors,'external_browser_requests':external}
        (args.out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(report,ensure_ascii=False));await browser.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--url',required=True)
    p.add_argument('--browser',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    asyncio.run(check(p.parse_args()))
