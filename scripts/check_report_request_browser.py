"""Exercise bounded report requests through the local browser, without models."""
import argparse
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright


async def check(args):
    cases = [('当归买回来放在哪里保存？', '贮藏', 1),
             ('整理当归的资料，想知道它主要用来做什么。', '功能与主治', 1),
             ('当归的功效和贮藏都需要，不过请先列保存要求。', '贮藏', 2)]
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=str(args.browser), headless=True, args=['--no-sandbox'])
        context = await browser.new_context()
        page = await context.new_page()
        errors = []; receipts = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        for question, first, count in cases:
            await page.goto(args.url)
            await page.locator('#local-research-panel > summary').click()
            await page.locator('[data-research-mode]').select_option('report')
            await page.locator('#local-research-form input').first.fill('当归')
            await page.locator('[data-report-question] input').fill(question)
            await page.locator('#local-research-form [type=submit]').click()
            await page.wait_for_function("document.querySelector('#local-research-output').innerText.includes('ETCM 基础属性')", timeout=45000)
            history = await (await context.request.get(args.url.rstrip('/')+'/api/research/jobs')).json()
            identifier = history['items'][0]['id']
            saved = await (await context.request.get(args.url.rstrip('/')+'/api/research/jobs/'+identifier)).json()
            result = saved['result']['text']
            assert result['status'] == 'candidates', result['status']
            assert result['hits'][0]['label'] == first
            assert len(result['hits']) == count
            assert all(h['evidence'] for h in result['hits'])
            assert result['retrieval']['request']['original_question'] == question
            assert result['retrieval']['missing_requested_fields'] == []
            receipts.append({'question': question, 'first_field': first, 'hits': count, 'source_preserved': True})
        await page.reload()
        await page.locator('#local-research-panel > summary').click()
        await page.locator('#local-research-panel details > summary').click()
        await page.locator('#local-research-history button').filter(has_text='回看').first.click()
        await page.wait_for_function("document.querySelector('#local-research-output').innerText.includes('ETCM 基础属性')")
        assert not errors, errors
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({'status':'passed','cases':receipts,'history_reloaded':True,'javascript_errors':errors},ensure_ascii=False,indent=2)+'\n')
        await browser.close()
        print('Report request browser passed')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', default='http://127.0.0.1:7882/')
    p.add_argument('--browser', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    asyncio.run(check(p.parse_args()))
