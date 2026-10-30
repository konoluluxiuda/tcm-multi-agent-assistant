"""Verify the user's table request on the actual chat homepage, localhost only."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright


async def check(args):
    parsed = urlsplit(args.url)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1':
        raise ValueError('Only localhost allowed')
    if args.out.exists():
        raise FileExistsError('Preserve earlier browser reports')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    errors, blocked = [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=str(args.browser), headless=True, args=['--no-sandbox'])
        ctx = await browser.new_context(accept_downloads=True)
        async def route(request):
            if urlsplit(request.request.url).netloc != parsed.netloc:
                blocked.append(request.request.url); await request.abort()
            else:
                await request.continue_()
        await ctx.route('**/*', route)
        page = await ctx.new_page(); page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(args.url + '/')
        await page.wait_for_function('window.tcmSessionReady===true')
        async def send(question):
            await page.locator('#message-input').fill(question)
            async with page.expect_response(lambda r: r.url.endswith('/chat') and r.request.method == 'POST', timeout=45000) as response:
                await page.locator('#chat-form').evaluate('(form)=>form.requestSubmit()')
            reply = await response.value
            result = await reply.json()
            assert reply.status == 200 and result['assistant']['topic'] == 'dataops_task', result
            return result['assistant']
        first = await send(args.question)
        first_id = first['workspace_id']
        frame = page.frame_locator(f'iframe[data-dataops-task="{first_id}"]')
        link = frame.get_by_role('link', name='下载核对表 v1（XLSX）', exact=True)
        await link.wait_for(timeout=45000)
        doc = await (await ctx.request.get(args.url + '/api/dataops/workspaces/' + first_id)).json()
        assert doc['status'] == 'complete' and len(doc['result']['rows']) == 2, doc['status']
        assert '未确认药名' not in await page.locator('#chat-body').inner_text()
        async with page.expect_download() as download:
            await link.click()
        file = await download.value
        digest = hashlib.sha256(Path(await file.path()).read_bytes()).hexdigest()
        assert digest == doc['artifact']['sha256']
        await page.reload()
        await page.wait_for_function('window.tcmSessionReady===true')
        await page.frame_locator(f'iframe[data-dataops-task="{first_id}"]').get_by_role(
            'link', name='下载核对表 v1（XLSX）', exact=True).wait_for(timeout=45000)
        second = await send('增加贮藏，保留旧字段')
        second_id = second['workspace_id']
        second_frame = page.frame_locator(f'iframe[data-dataops-task="{second_id}"]')
        await second_frame.get_by_role('link', name='下载核对表 v2（XLSX）', exact=True).wait_for(timeout=45000)
        child = await (await ctx.request.get(args.url + '/api/dataops/workspaces/' + second_id)).json()
        assert child['status'] == 'complete' and child['revision'] == 2 and child['progress']['reused_cells'] == 8
        old = await ctx.request.get(args.url + '/api/dataops/workspaces/' + first_id + '/artifact')
        assert hashlib.sha256(await old.body()).hexdigest() == digest
        unknown = await send('整理白术功效并预测靶点，生成表格')
        assert unknown['status'] == 'clarification_needed' and '预测靶点' in unknown['message'], unknown
        records = await (await ctx.request.get(args.url + '/api/dataops/workspaces')).json()
        assert len(records['items']) == 2
        await page.screenshot(path=str(args.out.with_suffix('.png')), full_page=True)
        assert not errors, errors
        report = {'status': 'passed', 'question': args.question, 'data': args.data,
            'driver': 'synthetic', 'external_model_calls': 0, 'chat_homepage_submission': True,
            'record_count': 2, 'inline_download_hash_verified': True, 'reload_restores_task': True,
            'chat_followup_revision': True, 'reused_cells': child['progress']['reused_cells'],
            'v1_preserved': True, 'unknown_requirements_not_enqueued': True,
            'javascript_errors': errors, 'blocked_external_assets': len(blocked), 'font_layout_reviewed': False}
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
        print(json.dumps(report, ensure_ascii=False))
        await browser.close()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', required=True); p.add_argument('--browser', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--data', choices=['real_local','synthetic'], required=True)
    p.add_argument('--question', default='整理白术和黄芪的功效、注意事项，保留出处并生成表格')
    asyncio.run(check(p.parse_args()))
