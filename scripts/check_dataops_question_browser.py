"""Question -> queue -> HITL -> delivery -> revision, against a localhost fixture."""
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
        raise FileExistsError('Preserve old browser reports')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    errors, external = [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=str(args.browser), headless=True, args=['--no-sandbox'])
        ctx = await browser.new_context(accept_downloads=True)
        async def route(request):
            if urlsplit(request.request.url).netloc != parsed.netloc:
                external.append(request.request.url); await request.abort()
            else:
                await request.continue_()
        await ctx.route('**/*', route)
        page = await ctx.new_page(); page.on('pageerror', lambda e: errors.append(str(e)))
        await page.goto(args.url + '/dataops')
        await page.locator('#naturalPanel').wait_for(state='visible')
        question = '整理Alpha的功效并预测靶点'
        await page.locator('#naturalQuestion').fill(question)
        async with page.expect_response(lambda r: r.url.endswith('/api/dataops/task-requests')) as response:
            await page.locator('#submitQuestion').click()
        assert (await response.value).status == 409
        await page.get_by_text('以下要求尚不能可靠处理：预测靶点', exact=False).wait_for()
        assert await page.locator('#naturalQuestion').input_value() == question
        assert (await (await ctx.request.get(args.url + '/api/dataops/workspaces')).json())['items'] == []
        await page.locator('#naturalQuestion').fill('整理Alpha和Gamma的功效、注意事项，保留出处并生成表格')
        async with page.expect_response(lambda r: r.url.endswith('/api/dataops/task-requests')) as response:
            await page.locator('#submitQuestion').click()
        first_id = (await (await response.value).json())['workspace']['id']
        choose = page.get_by_role('button', name='丙草 · entry-2', exact=True)
        await choose.wait_for(timeout=45000)
        await page.reload()
        await page.get_by_role('button', name='批量资料核对 · v1 · waiting_input', exact=True).click()
        await choose.wait_for(); await choose.click()
        await page.get_by_role('link', name='下载核对表 v1（XLSX）', exact=True).wait_for(timeout=45000)
        first = await (await ctx.request.get(args.url + '/api/dataops/workspaces/' + first_id)).json()
        before = await ctx.request.get(args.url + f'/api/dataops/workspaces/{first_id}/artifact')
        digest = hashlib.sha256(await before.body()).hexdigest()
        assert digest == first['artifact']['sha256']
        await page.locator('#naturalQuestion').fill('增加贮藏，保留旧字段')
        await page.locator('#reviseQuestion').wait_for(state='visible')
        async with page.expect_response(lambda r: r.url.endswith('/api/dataops/task-requests')) as response:
            await page.locator('#reviseQuestion').click()
        second_id = (await (await response.value).json())['workspace']['id']
        await page.get_by_role('link', name='下载核对表 v2（XLSX）', exact=True).wait_for(timeout=45000)
        second = await (await ctx.request.get(args.url + '/api/dataops/workspaces/' + second_id)).json()
        assert second['revision'] == 2 and second['progress']['reused_cells'] == 8
        assert second['progress']['leaf_reads'] == 3
        assert second['result']['diff']
        async with page.expect_download() as download:
            await page.get_by_role('link', name='下载核对表 v2（XLSX）', exact=True).click()
        file = await download.value
        assert hashlib.sha256(Path(await file.path()).read_bytes()).hexdigest() == second['artifact']['sha256']
        old = await ctx.request.get(args.url + f'/api/dataops/workspaces/{first_id}/artifact')
        assert hashlib.sha256(await old.body()).hexdigest() == digest
        await page.reload()
        await page.get_by_role('button', name='批量资料核对 · v2 · complete', exact=True).click()
        await page.get_by_role('link', name='下载核对表 v2（XLSX）', exact=True).wait_for()
        await page.screenshot(path=str(args.out.with_suffix('.png')), full_page=True)
        await browser.close()
    assert not errors and not external, (errors, external)
    report = {'status': 'passed', 'kind': 'synthetic_question_ui_contract', 'external_model_calls': 0,
        'unknown_requirements_not_enqueued': True, 'original_question_kept': True,
        'question_to_delivery': True, 'persistent_candidate_answer': True, 'incremental_revision': True,
        'v1_preserved': True, 'download_hash_checked': True, 'reload_finished': True,
        'javascript_errors': errors, 'external_requests': external, 'font_layout_reviewed': False}
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', required=True); p.add_argument('--browser', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    asyncio.run(check(p.parse_args()))
