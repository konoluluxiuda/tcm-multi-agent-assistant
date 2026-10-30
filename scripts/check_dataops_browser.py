"""Local-only D1 browser acceptance: selection, workbook, replay and owner boundary."""
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
        raise ValueError('Use a local origin')
    origin = args.url.rstrip('/')
    protocol = json.loads(args.protocol.read_text())
    ids = protocol['goal']['query']['source_ids']
    errors, external = [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=str(args.browser), headless=True, args=['--no-sandbox'])
        context = await browser.new_context(accept_downloads=True)
        async def local(route):
            if urlsplit(route.request.url).netloc == parsed.netloc:
                await route.continue_()
            else:
                external.append(route.request.resource_type)
                await route.abort()
        await context.route('**/*', local)
        page = await context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(origin + '/dataops')
        await page.get_by_text('按名称或来源ID指定记录', exact=True).click()
        await page.locator('#sourceIds').fill('\n'.join(ids))
        await page.locator('#title').fill('D1真实20条网页验收')
        async with page.expect_response(lambda r: r.url.endswith('/api/dataops/workspaces') and r.request.method == 'POST', timeout=90000) as response:
            await page.locator('#run').click()
        result = await response.value
        assert result.status == 201, await result.text()
        doc = await result.json()
        identifier = doc['id']
        request_body = result.request.post_data_json
        assert len(doc['result']['rows']) == 20
        await page.locator('#result table tr').nth(20).wait_for()
        assert await page.locator('#result table tr').count() == 21
        assert await page.locator('#result details[open]').count() == 0
        async with page.expect_download() as download:
            await page.locator('#result a').click()
        downloaded = await download.value
        path = await downloaded.path()
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == doc['artifact']['sha256']
        with zipfile.ZipFile(path) as z:
            assert len([n for n in z.namelist() if n.startswith('xl/worksheets/sheet') and n.endswith('.xml')]) == 4
        replay = await context.request.post(origin + '/api/dataops/workspaces', data=request_body)
        assert replay.status == 200 and (await replay.json())['id'] == identifier
        await page.reload()
        await page.get_by_role('button', name='D1真实20条网页验收 · v1 · complete').first.click()
        await page.locator('#result table tr').nth(20).wait_for()
        assert (await page.locator('#result a').get_attribute('href')).endswith(identifier + '/artifact')
        other = await browser.new_context()
        await other.request.get(origin + '/dataops')
        assert (await other.request.get(origin + '/api/dataops/workspaces/' + identifier)).status == 404
        assert (await other.request.get(origin + '/api/dataops/workspaces/' + identifier + '/artifact')).status == 404
        await other.close()
        # A user can supply familiar names without finding opaque IDs first.
        await page.get_by_text('按名称或来源ID指定记录', exact=True).click()
        await page.locator('#sourceIds').fill('白术\n黄芪')
        await page.locator('#title').fill('D1中文名单验收')
        async with page.expect_response(lambda r: r.url.endswith('/api/dataops/workspaces') and r.request.method == 'POST', timeout=90000) as names_response:
            await page.locator('#run').click()
        named_doc = await (await names_response.value).json()
        assert len(named_doc['result']['rows']) == 2, named_doc
        assert set(named_doc['result']['query']['unknown_names']) == set()
        assert all(r['cells']['功能与主治']['value'] and r['cells']['功能与主治']['status'] == 'candidate_scope_value'
                   for r in named_doc['result']['rows']), 'Do not misreport prepared-slice fields as absent'
        await page.locator('#result table tr').nth(2).wait_for()
        await page.screenshot(path=str(args.out.with_suffix('.png')), full_page=True)
        # Selection and filter changes are visible and cannot silently reuse stale IDs.
        await page.locator('#sourceIds').fill('')
        await page.locator('#limit').fill('2')
        await page.locator('#browse').click()
        await page.locator('#records tr').nth(2).wait_for()
        await page.locator('#filterField').select_option('family')
        assert await page.locator('#records tr').count() == 0
        await page.goto(origin + '/')
        assert await page.locator('a[href="/dataops"]').count() == 1
        assert await page.locator('#workspace-tab-literature').count() == 0
        await browser.close()
    assert not errors, errors
    report = {'status': 'passed', 'source_records': 20, 'workspace_id': identifier,
              'four_sheet_download': True, 'download_sha256_verified': True, 'idempotent_replay': True,
              'restored_after_refresh': True, 'other_owner_denied': True, 'stale_selection_cleared': True,
              'retired_menu_removed': True, 'javascript_errors': errors, 'blocked_external_assets': len(external),
              'chinese_name_list': ['白术', '黄芪'],
              'prepared_slice_scope_preserved': True, 'font_layout_reviewed': False,
              'model_calls': 0}
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', required=True)
    p.add_argument('--protocol', type=Path, default=Path('var/dataops_d1_scope_final/protocol.json'))
    p.add_argument('--browser', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args(); args.out.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(check(args))
