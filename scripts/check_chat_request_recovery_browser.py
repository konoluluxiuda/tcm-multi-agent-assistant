"""Offline browser fault injection against real ASGI, fixture sources and fixed execution."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit


async def check(args):
    from playwright.async_api import async_playwright
    from tests.test_preset_requests import PresetHTTPTests

    fixture = PresetHTTPTests('test_single_source_table_is_downloadable_without_added_columns')
    fixture.setUp()
    requests, errors, external = [], [], []
    fault = None
    checks = []
    origin = 'http://127.0.0.1'
    fixture.client.base_url = origin
    fixture.client.cookies.clear()
    try:
        with patch.dict(os.environ, {'ENABLE_DATAOPS_RESEARCH_GOAL_MODEL': '0'}), \
             patch.object(fixture.app, 'process_query', wraps=fixture.app.process_query) as execute:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(executable_path=str(args.browser),
                                                   headless=True, args=['--no-sandbox'])
                context = await browser.new_context()

                async def route(route):
                    nonlocal fault
                    request = route.request
                    parsed = urlsplit(request.url)
                    if parsed.netloc != '127.0.0.1':
                        external.append(request.url)
                        await route.abort()
                        return
                    mode = None
                    if parsed.path == '/chat' and request.method == 'POST':
                        requests.append(request.post_data_json)
                        mode, fault = fault, None
                        if mode == 'before':
                            await route.abort('failed')
                            return
                    headers = await request.all_headers()
                    response = await asyncio.to_thread(fixture.client.request, request.method,
                        request.url, headers=headers, content=request.post_data_buffer)
                    if mode == 'after':
                        assert response.status_code == 200, response.text
                        await route.abort('failed')
                        return
                    outgoing = {k: v for k, v in response.headers.items()
                                if k.lower() not in ('content-encoding', 'content-length')}
                    await route.fulfill(status=response.status_code, headers=outgoing, body=response.content)

                await context.route('**/*', route)
                page = await context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))

                async def ready():
                    await page.wait_for_function('window.tcmSessionReady === true')
                    await page.wait_for_function('!document.querySelector("#message-input").disabled')

                async def send(question):
                    await page.locator('#message-input').fill(question)
                    await page.locator('#chat-form').evaluate('(form) => form.requestSubmit()')
                    await page.wait_for_function('!document.querySelector("#message-input").disabled')

                async def pending():
                    return await page.evaluate('JSON.parse(sessionStorage.getItem("tcm-chat-pending-v1"))')

                await page.goto(origin)
                await ready()
                query = '分别告诉我Alpha和Beta的ETCM药用部位。'
                fault = 'before'
                await send(query)
                saved = await pending()
                assert saved and execute.call_count == 0
                count = len(requests)
                await page.reload()
                await ready()
                assert len(requests) == count and await pending() == saved
                await page.locator('#chat-pending-retry').click()
                await page.wait_for_function('!document.querySelector("#message-input").disabled')
                assert requests[-1] == saved and execute.call_count == 2
                assert await pending() is None
                checks.append('unreceived_request_refresh_does_not_send_manual_retry_reuses_body')

                fault = 'after'
                await send(query)
                assert requests[-1]['request_key'] != saved['request_key']
                assert await pending() is not None and execute.call_count == 4
                count = len(requests)
                await page.reload()
                await ready()
                assert len(requests) == count and execute.call_count == 4
                assert await pending() is None
                assert 'Alpha' in await page.locator('#chat-body').inner_text()
                assert 'Beta' in await page.locator('#chat-body').inner_text()
                checks.append('committed_query_response_lost_refresh_restores_without_reexecution')

                fault = 'after'
                await send('把Alpha和Beta的ETCM药用部位生成表格。')
                assert await pending() is not None
                items = fixture.client.get('/api/dataops/workspaces').json()['items']
                assert len(items) == 1
                identifier = items[0]['id']
                doc = await asyncio.to_thread(fixture.wait, identifier)
                assert doc['status'] == 'complete'
                path = '/api/dataops/workspaces/' + identifier + '/artifact'
                digest = hashlib.sha256(fixture.client.get(path).content).hexdigest()
                count = len(requests)
                await page.reload()
                await ready()
                assert await pending() is None and len(requests) == count
                assert len(fixture.client.get('/api/dataops/workspaces').json()['items']) == 1
                assert hashlib.sha256(fixture.client.get(path).content).hexdigest() == digest
                assert await page.locator(f'iframe[data-dataops-task="{identifier}"]').count() == 1
                checks.append('committed_table_response_lost_restores_same_workspace_and_xlsx')

                fault = 'before'
                await send(query)
                old = await pending()
                await send('查询Alpha的ETCM科名。')
                assert requests[-1]['request_key'] != old['request_key']
                assert await pending() is None
                checks.append('editing_question_creates_new_request')

                fault = 'before'
                await send(query)
                old = await pending()
                fixture.client.delete('/conversation')  # another tab clears the shared server session
                await page.reload()
                await ready()
                assert await pending() is None
                assert not await page.locator('#chat-pending').is_visible()
                checks.append('changed_session_discards_old_pending_request')

                await page.evaluate('sessionStorage.setItem("tcm-chat-pending-v1", "broken json")')
                await page.reload()
                await ready()
                assert await pending() is None
                checks.append('malformed_pending_storage_is_discarded')

                count = len(requests)
                await page.evaluate('''() => {
                    window.savedStorageSetItem = Storage.prototype.setItem;
                    Storage.prototype.setItem = function () { throw new DOMException('blocked', 'SecurityError'); };
                }''')
                try:
                    await send(query)
                    assert len(requests) == count
                    assert await page.locator('#message-input').input_value() == query
                    assert '本次未发送' in await page.locator('#memory-status').inner_text()
                finally:
                    await page.evaluate('() => { Storage.prototype.setItem = window.savedStorageSetItem; }')
                checks.append('storage_write_failure_preserves_draft_and_prevents_post')
                assert not errors, errors
                await browser.close()
    finally:
        fixture.doCleanups()
    report = {'status': 'passed', 'data': 'synthetic', 'execution': 'fixed',
              'model_calls': 0, 'checks': checks, 'javascript_errors': errors,
              'external_browser_requests_blocked': external,
              'browser_post_attempts': len(requests), 'query_tool_calls': execute.call_count,
              'visual_layout_reviewed': False}
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError('Use a fresh acceptance report path')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    import dotenv
    from scripts.run_offline_ci import test_environment
    with patch.dict(os.environ, test_environment(), clear=True), patch.object(dotenv, 'load_dotenv', return_value=False):
        asyncio.run(check(args))


if __name__ == '__main__':
    main()
