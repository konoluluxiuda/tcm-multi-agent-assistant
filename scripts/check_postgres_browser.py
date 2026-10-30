"""Kill and restart a synthetic preview, then resume via the real history button."""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[1]


async def check(args):
    args.out.mkdir(parents=True,exist_ok=False)
    env={**os.environ,'POSTGRES_FIXTURE_DIR':str(args.out.resolve())}
    command=[str(args.server_python),'-m','tests.postgres_http_fixture','--port',str(args.port),
             '--state',str(args.out/'state'),'--diseases','var/disease_catalog_v1/build/diseases.sqlite3']
    log=(args.out/'server.log').open('w')
    proc=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=log)
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch(executable_path=str(args.browser),headless=True,args=['--no-sandbox'])
            context=await browser.new_context();page=await context.new_page();errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            url=f'http://127.0.0.1:{args.port}/'
            async def ready():
                for _ in range(100):
                    try:
                        if (await context.request.get(url,timeout=1000)).ok:return
                    except Exception:pass
                    await asyncio.sleep(.1)
                raise RuntimeError('Preview did not start')
            await ready();await page.goto(url)
            await page.locator('#local-research-panel > summary').click()
            await page.locator('[data-research-mode]').select_option('compare' if args.pair else 'report')
            await page.locator('#local-research-form input').first.fill('a' if args.pair else '示例叶')
            if args.pair:await page.locator('[data-second-herb] input').fill('b')
            await page.locator('[data-report-question] input').fill('功效')
            async with page.expect_response(lambda r:r.url.endswith('/api/research/jobs/compare' if args.pair else '/api/research/jobs/report')) as response:
                await page.locator('#local-research-form [type=submit]').click()
            submitted=await (await response.value).json();identifier=submitted['run_id']
            for _ in range(100):
                saved=await (await context.request.get(url+'api/research/jobs/'+identifier)).json()
                if (saved.get('snapshot',{}).get('tool_calls',0)>=3 if args.pair else saved.get('snapshot',{}).get('last_node')=='text_research'):break
                await asyncio.sleep(.1)
            else:raise AssertionError('Text branch did not complete')
            # Allow LangGraph pending writes to flush before killing the whole HTTP process.
            await asyncio.sleep(.5)
            proc.kill();proc.wait(timeout=5)
            await asyncio.sleep(31)  # Real lease expiry, not a forged database timestamp.
            (args.out/'release').touch()
            proc=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=log)
            await ready();await page.reload()
            await page.locator('#local-research-panel > summary').click()
            await page.locator('#local-research-panel details > summary').click()
            await page.locator('#local-research-refresh').click()
            await page.get_by_role('button',name='继续执行',exact=True).click(timeout=10000)
            await page.wait_for_function("expected => document.querySelector('#local-research-output').innerText.includes(expected)",arg='synthetic-a' if args.pair else '合成恢复验收文本',timeout=30000)
            saved=await (await context.request.get(url+'api/research/jobs/'+identifier)).json()
            assert saved['status']=='complete',saved['status']
            counts=Counter(json.loads(x)['branch'] for x in (args.out/'calls.jsonl').read_text().splitlines())
            expected={'a_text':1,'a_structured':1,'b_text':1,'b_structured':2,'target':1} if args.pair else {'text':1,'structured':2}
            assert counts==expected,counts
            assert not errors,errors
            receipt={'status':'passed','real_http_process_sigkill':True,'lease_expiry_seconds':31,
                     'history_resume_button':True,'same_cookie_after_restart':True,'branch_calls':dict(counts),
                     'javascript_errors':errors,'external_model_calls':0,'data':'synthetic_only'}
            (args.out/'summary.json').write_text(json.dumps(receipt,indent=2)+'\n')
            await page.screenshot(path=str(args.out/'browser.png'),full_page=True)
            await browser.close();print(json.dumps(receipt))
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:proc.wait(timeout=10)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
        log.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--server-python',type=Path,required=True);p.add_argument('--browser',type=Path,required=True)
    p.add_argument('--port',type=int,default=7885);p.add_argument('--pair',action='store_true')
    asyncio.run(check(p.parse_args()))
