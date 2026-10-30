"""Local browser acceptance for D2 observation, HITL, revision and reload."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
import zipfile

from playwright.async_api import async_playwright


async def check(args):
    parsed = urlsplit(args.url)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1':
        raise ValueError('Only localhost allowed')
    if args.out.exists():
        raise FileExistsError('Use a fresh report path')
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
        await page.locator('#agentPanel').wait_for(state='visible')
        await page.get_by_text('按名称或来源ID指定记录', exact=True).click()
        await page.locator('#sourceIds').fill('Alpha\nGamma')
        await page.locator('#title').fill('D2浏览器合成核对')
        async with page.expect_response(lambda r: r.url.endswith('/api/dataops/agent-runs') and r.request.method == 'POST') as response:
            await page.locator('#runAgent').click()
        doc = (await (await response.value).json())['workspace']
        identifier = doc['id']
        choose = page.get_by_role('button', name='丙草 · entry-2', exact=True)
        await choose.wait_for(timeout=45000)
        await page.reload()
        await page.get_by_role('button', name='D2浏览器合成核对 · v1 · waiting_input').click()
        await choose.wait_for()
        await choose.click()
        await page.get_by_role('link', name='下载核对表 v1（XLSX）', exact=True).wait_for(timeout=45000)
        first = await (await ctx.request.get(args.url + '/api/dataops/workspaces/' + identifier)).json()
        assert first['status'] == 'complete'
        assert next(r for r in first['result']['rows'] if r['name']=='Gamma')['cells']['功能与主治']['value']=='测试原文2'
        await page.locator('#fields input[value="贮藏"]').check()
        async with page.expect_response(lambda r: r.url.endswith('/api/dataops/agent-runs') and r.request.method == 'POST') as response:
            await page.locator('#revise').click()
        second_id = (await (await response.value).json())['workspace']['id']
        await page.get_by_role('link', name='下载核对表 v2（XLSX）', exact=True).wait_for(timeout=45000)
        second = await (await ctx.request.get(args.url + '/api/dataops/workspaces/' + second_id)).json()
        assert second['revision'] == 2 and second['progress']['reused_cells'] == 8
        async with page.expect_download() as download:
            await page.get_by_role('link', name='下载核对表 v2（XLSX）', exact=True).click()
        file = await download.value; path = Path(await file.path())
        assert hashlib.sha256(path.read_bytes()).hexdigest() == second['artifact']['sha256']
        with zipfile.ZipFile(path) as book:
            assert len([n for n in book.namelist() if n.startswith('xl/worksheets/sheet') and n.endswith('.xml')]) == 5
        old = await ctx.request.get(args.url + f'/api/dataops/workspaces/{identifier}/artifact')
        assert hashlib.sha256(await old.body()).hexdigest() == first['artifact']['sha256']
        other = await browser.new_context()
        await other.request.get(args.url + '/dataops')
        assert (await other.request.get(args.url + '/api/dataops/agent-runs/' + identifier)).status == 404
        await other.close()
        await page.reload()
        await page.get_by_role('button', name='D2浏览器合成核对 · v2 · complete').click()
        await page.get_by_role('link', name='下载核对表 v2（XLSX）', exact=True).wait_for()
        await page.screenshot(path=str(args.out.with_suffix('.png')), full_page=True)
        await browser.close()
    assert not errors and not external, (errors, external)
    report = {'status': 'passed', 'fixture': 'synthetic_data_and_model', 'external_model_calls': 0,
        'persistent_human_decision_ui': True, 'reload_waiting_and_finished': True,
        'incremental_v2': True, 'v1_preserved': True, 'five_sheets_download_hash_checked': True,
        'owner_isolation': True, 'javascript_errors': errors, 'external_requests': external,
        'font_layout_reviewed': False, 'first_workspace': identifier, 'second_workspace': second_id}
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', required=True); p.add_argument('--browser', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    asyncio.run(check(p.parse_args()))
