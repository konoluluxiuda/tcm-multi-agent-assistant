"""Browser/HTTP/PostgreSQL acceptance for full pair pagination using synthetic catalogs."""
import argparse
import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]


async def check(args):
    args.out.mkdir(parents=True, exist_ok=False)
    env = {**os.environ, 'PAIR_PAGINATION_FIXTURE_DIR': str(args.out.resolve())}
    command = [str(args.server_python), '-m', 'tests.pair_pagination_http_fixture', '--port', str(args.port),
               '--state', str(args.out/'state'), '--diseases', 'var/disease_catalog_v1/build/diseases.sqlite3']
    log = (args.out/'server.log').open('w')
    proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=log)
    url = f'http://127.0.0.1:{args.port}'
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(executable_path=str(args.browser), headless=True, args=['--no-sandbox'])
            context = await browser.new_context(accept_downloads=True)
            page = await context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            async def route(request):
                if not request.request.url.startswith(url+'/'):
                    await request.abort()
                else:
                    await request.continue_()
            await page.route('**/*', route)
            async def ready():
                for _ in range(150):
                    if proc.poll() is not None:
                        raise RuntimeError('Fixture server stopped; inspect server.log')
                    try:
                        if (await context.request.get(url+'/', timeout=1000)).ok:
                            return
                    except Exception:
                        pass
                    await asyncio.sleep(.1)
                raise RuntimeError('Server startup timeout')
            async def submit(question):
                await page.locator('[data-research-mode]').select_option('compare')
                await page.locator('#local-research-form input').first.fill('Alpha')
                await page.locator('[data-second-herb] input').fill('Beta')
                await page.locator('[data-report-question] input').fill(question)
                async with page.expect_response(lambda r:r.url.endswith('/api/research/jobs/compare')) as response:
                    await page.locator('#local-research-form [type=submit]').click()
                data = await (await response.value).json()
                return data['run_id']
            async def saved(identifier):
                return await (await context.request.get(url+'/api/research/jobs/'+identifier)).json()
            async def terminal(identifier):
                for _ in range(200):
                    data = await saved(identifier)
                    if data['status'] in ('complete', 'partial', 'failed'):
                        return data
                    await asyncio.sleep(.1)
                raise AssertionError('Task did not finish')
            await ready()
            await page.goto(url)
            await page.locator('#local-research-panel > summary').click()
            identifier = await submit('比较功能与主治、注意事项，列出全部共同靶点')
            for _ in range(150):
                calls = (args.out/'calls.jsonl').read_text() if (args.out/'calls.jsonl').exists() else ''
                if 'target_2' in calls:
                    break
                await asyncio.sleep(.1)
            else:
                raise AssertionError('Second page was not dispatched')
            proc.kill()
            proc.wait(timeout=5)
            print('Killed test HTTP service during page 2; waiting for the real 31-second lease expiry.', flush=True)
            await asyncio.sleep(31)
            (args.out/'release').touch()
            proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=log)
            await ready()
            await page.reload()
            await page.locator('#local-research-panel > summary').click()
            await page.locator('#local-research-panel details > summary').click()
            await page.locator('#local-research-refresh').click()
            await page.get_by_role('button', name='继续执行', exact=True).click(timeout=10000)
            data = await terminal(identifier)
            assert data['status'] == 'complete', data.get('reason')
            await page.wait_for_function("document.querySelector('#local-research-output').innerText.includes('当前显示 23 个')")
            counts = Counter(json.loads(line)['branch'] for line in (args.out/'calls.jsonl').read_text().splitlines())
            assert counts == {'Alpha_text':1, 'Alpha_structured':1, 'Beta_text':1, 'Beta_structured':1,
                              'target_1':1, 'target_2':2}, counts
            async with page.expect_download() as download:
                await page.get_by_role('button', name='下载报告与出处 JSON', exact=True).click()
            await (await download.value).save_as(args.out/'download.json')
            exported = json.loads((args.out/'download.json').read_text())
            assert len(exported['targets']['items']) == 23
            assert all(len(i['sources']) == 2 for i in exported['targets']['items'])
            await page.screenshot(path=str(args.out/'complete.png'), full_page=True)

            (args.out/'fail_second').touch()
            partial_id = await submit('比较功能与主治、注意事项，列出所有共同靶点')
            partial = await terminal(partial_id)
            assert partial['status'] == 'partial', partial.get('reason')
            await page.wait_for_function("document.querySelector('#local-research-output').innerText.includes('查询未完成，以下保留已获取的共同靶点')")
            assert '当前显示 20 个' in await page.locator('#local-research-output').inner_text()
            await page.get_by_role('button', name='在聊天中查询后续靶点', exact=True).wait_for()
            await page.screenshot(path=str(args.out/'partial.png'), full_page=True)
            assert not errors, errors
            receipt = {'status':'passed', 'data':'synthetic_only', 'external_model_calls':0,
                       'real_postgres':True, 'real_http_sigkill':True, 'lease_expiry_seconds':31,
                       'same_browser_cookie':True, 'history_resume_button':True,
                       'resumed_branch_calls':dict(counts), 'complete_targets':23,
                       'downloaded_targets':23, 'partial_targets_visible':20,
                       'next_page_button_visible':True, 'javascript_errors':errors}
            (args.out/'summary.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n')
            print(json.dumps(receipt, ensure_ascii=False))
            await browser.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait()
        log.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--server-python', type=Path, required=True)
    parser.add_argument('--browser', type=Path, required=True)
    parser.add_argument('--port', type=int, default=7893)
    asyncio.run(check(parser.parse_args()))
