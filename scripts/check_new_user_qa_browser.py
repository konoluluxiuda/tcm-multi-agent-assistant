"""Local-only regression of new-user chat and the manual DataOps entry.

Uses fresh browser cookies and creates one workspace in the target instance.
Requires prepared local ETCM/pharmacopoeia data and an isolated Playwright runtime.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright


async def check(args):
    origin = urlsplit(args.url)
    if origin.scheme != 'http' or origin.hostname != '127.0.0.1' or origin.path not in ('', '/'):
        raise ValueError('Only a localhost HTTP origin is allowed')
    base = args.url.rstrip('/')
    if args.out.exists():
        raise FileExistsError('Preserve earlier reports')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    errors, external, failed_http = [], [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=str(args.browser), headless=True, args=['--no-sandbox'])
        ctx = await browser.new_context(accept_downloads=True)
        async def route(request):
            if urlsplit(request.request.url).netloc != origin.netloc:
                external.append(request.request.url)
                await request.abort()
            else:
                await request.continue_()
        await ctx.route('**/*', route)
        page = await ctx.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('response', lambda response: failed_http.append(response.url) if response.status >= 400 else None)
        await page.goto(base + '/')
        await page.wait_for_function('window.tcmSessionReady===true')
        runtime = await (await ctx.request.get(base + '/api/runtime')).json()
        assert runtime['restart_required'] is False, runtime
        assert runtime['intent_model_enabled'] is False, 'Use the local preview without intent-model calls'
        async def send(question):
            await page.locator('#message-input').fill(question)
            async with page.expect_response(lambda r: r.url.endswith('/chat') and r.request.method == 'POST') as response:
                await page.locator('#chat-form').evaluate('(form)=>form.requestSubmit()')
            reply = await response.value
            data = await reply.json()
            assert reply.status == 200, data
            assert data.get('model_calls', 0) == 0, 'This check must not call a model'
            await page.wait_for_function('!document.querySelector("#message-input").disabled')
            return data
        help_result = await send('当前能做什么')
        assert help_result['assistant']['topic'] == 'capabilities'
        fields = await send('查看白术的药用部位和科名')
        assert fields['routing']['reason'] == 'structured_multi_field_query'
        assert fields['tcm']['requested_fields'] == ['medicinal_part', 'family']
        assert '药用部位' in fields['response'] and '科名' in fields['response']
        ingredients = await send('白术有哪些成分')
        assert ingredients['assistant']['status'] == 'ok'
        assert ingredients['assistant']['relations']['groups'][0]['selection_method'] == 'unique_local_candidate'
        medicine = await send('黄芪有什么作用')
        assert '【功能与主治】' in medicine['response'] and '出处：来源PDF' in medicine['response']
        assert await page.locator('#chat-body table').count() > 0
        assert await page.locator('body').evaluate('(body)=>getComputedStyle(body).fontFamily')
        await page.screenshot(path=str(args.out.with_suffix('.png')), full_page=True)
        await page.goto(base + '/dataops')
        await page.get_by_text('按名称或来源ID指定记录', exact=True).click()
        await page.locator('#sourceIds').fill('白术\n黄芪')
        await page.locator('#title').fill('新用户入口本地核验')
        async with page.expect_response(lambda r: r.url.endswith('/api/dataops/workspaces') and r.request.method == 'POST', timeout=60000) as response:
            await page.locator('#run').click()
        reply = await response.value
        doc = await reply.json()
        assert reply.status == 201 and doc['status'] == 'complete', (reply.status, doc.get('status'), doc.get('error'))
        assert len(doc['result']['rows']) == 2
        link = page.get_by_role('link', name='下载核对表 v1（XLSX）', exact=True)
        await link.wait_for()
        async with page.expect_download() as download:
            await link.click()
        downloaded = await download.value
        assert hashlib.sha256(Path(await downloaded.path()).read_bytes()).hexdigest() == doc['artifact']['sha256']
        await page.reload()
        await page.locator('#history').get_by_role('button', name='新用户入口本地核验 · v1 · complete', exact=True).wait_for()
        assert not errors, errors
        assert not external, external
        assert not failed_http, failed_http
        result = dict(status='passed', port=origin.port, runtime_fresh=True,
                      capability_chat=True, multi_field_without_queue=True,
                      unique_ingredient_source=True, readable_api_text=True,
                      manual_workbench_download=True, download_sha256_verified=True,
                      reload_restores_history=True, javascript_errors=errors,
                      external_requests=external, failed_http=failed_http,
                      external_model_calls=0)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
        print(json.dumps(result, ensure_ascii=False))
        await browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--browser', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    asyncio.run(check(parser.parse_args()))
