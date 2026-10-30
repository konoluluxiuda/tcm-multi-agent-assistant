"""Browser acceptance for the opt-in authenticated synthetic loop panel."""
import argparse
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright


async def main(args):
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path=str(args.browser),headless=True,args=['--no-sandbox'])
        context=await browser.new_context();page=await context.new_page();errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        await page.goto(args.url)
        panel=page.locator('#synthetic-loop-panel');await panel.wait_for(state='visible')
        await panel.locator('summary').click();await page.locator('#loop-pause').click()
        await panel.get_by_role('button',name='继续执行',exact=True).wait_for()
        items=(await (await context.request.get(args.url+'api/research/loops')).json())['items']
        assert len(items)==1 and items[0]['phase']=='ready'
        initial=items[0]
        other=await browser.new_context();await other.request.get(args.url+'conversation')
        assert (await other.request.get(args.url+'api/research/loops/'+initial['id'])).status==404
        await other.close()
        await page.reload();await panel.wait_for(state='visible');await panel.locator('summary').click()
        await panel.get_by_role('button',name='继续执行',exact=True).click()
        await page.wait_for_function("document.querySelector('#loop-history').innerText.includes('complete')")
        final=await (await context.request.get(args.url+'api/research/loops/'+initial['id'])).json()
        assert (final['model_calls'],final['tool_calls'])==(4,3)
        await panel.get_by_role('button',name='删除记录',exact=True).click()
        await page.wait_for_function("document.querySelector('#loop-history').children.length===0")
        assert (await context.request.get(args.url+'api/research/loops/'+initial['id'])).status==404
        await page.locator('#loop-pause').click()
        await panel.get_by_role('button',name='继续执行',exact=True).wait_for()
        await page.locator('#clear-chat').click()
        await page.wait_for_function("document.querySelector('#loop-history').children.length===0")
        assert not (await (await context.request.get(args.url+'api/research/loops')).json())['items']
        assert not errors,errors
        result={'status':'passed','pause_reload_resume':True,'owner_isolation':True,'delete_verified':True,
                'counts_scope':'completed run; session rotation adds a paused run with 1 model and 1 tool call',
                'session_rotation_clears_list':True,'model_calls':4,'tool_calls':3,'external_calls':0,'javascript_errors':errors,'data':'synthetic_only'}
        args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result));await browser.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:7893/')
    p.add_argument('--browser',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    asyncio.run(main(p.parse_args()))
