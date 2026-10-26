"""Local browser acceptance for explicit project memory; no external models."""
import argparse
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright


async def check(args):
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path=str(args.browser),headless=True,args=['--no-sandbox'])
        context=await browser.new_context();page=await context.new_page();errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        async def open_panel():
            await page.goto(args.url)
            await page.locator('#local-research-panel > summary').click()
        await open_panel()
        f=page.locator('#project-memory-form')
        await f.locator('[name=title]').fill('验收项目')
        await f.locator('[name=herb]').fill('当归')
        await f.locator('[name=question]').fill('贮藏')
        await f.locator('[type=submit]').click()
        await page.wait_for_function("document.querySelector('#project-status').textContent==='项目已保存。'")
        # A new page lifecycle, same signed identity: memory is fetched from server.
        await open_panel()
        await page.locator('#project-list button').filter(has_text='用于报告').click()
        await page.wait_for_function("document.querySelector('[data-report-question] input').value==='贮藏'")
        assert await page.locator('#local-research-form input').first.input_value()=='当归'
        assert await page.locator('[data-research-mode]').input_value()=='report'
        await page.locator('#local-research-form [type=submit]').click()
        await page.wait_for_function("document.querySelector('#local-research-output').innerText.includes('ETCM 基础属性')",timeout=60000)
        history=await (await context.request.get(args.url+'api/research/jobs')).json()
        job=await (await context.request.get(args.url+'api/research/jobs/'+history['items'][0]['id'])).json()
        assert job['payload']['herb']=='当归' and job['payload']['question']=='贮藏'
        assert job['result']['text']['hits'][0]['label']=='贮藏'
        # Another browser identity cannot read this project.
        other=await browser.new_context();otherpage=await other.new_page();await otherpage.goto(args.url)
        others=await (await other.request.get(args.url+'api/research/projects')).json()
        assert others=={'items':[]},others
        await other.close()
        await page.locator('#project-list button').filter(has_text='编辑项目').click()
        await page.wait_for_function("document.querySelector('#project-memory-form [name=title]').value==='验收项目'")
        await f.locator('[name=question]').fill('功效')
        await f.locator('[type=submit]').click()
        await page.wait_for_function("document.querySelector('#project-status').textContent==='项目已保存。'")
        # Previously recalled revision is now stale; cannot silently use it.
        await page.locator('#local-research-form [type=submit]').click()
        await page.wait_for_function("document.querySelector('#local-research-output').innerText.includes('项目已修改')")
        await page.locator('#project-list button').filter(has_text='用于报告').click()
        await page.wait_for_function("document.querySelector('[data-report-question] input').value==='功效'")
        await page.locator('#project-list button').filter(has_text='删除项目').click()
        await page.wait_for_function("document.querySelector('#project-status').textContent.includes('项目已删除')")
        await open_panel()
        await page.wait_for_function("document.querySelector('#project-list').textContent.includes('暂无项目')")
        assert not errors,errors
        report={'status':'passed','reload_recall':True,'real_local_report':True,'source_field':'贮藏','other_browser_isolated':True,'stale_recall_blocked':True,'delete_not_recalled':True,'javascript_errors':errors,'external_model_calls':0,
                'comparison':{'kind':'fixed_workflow_input_count_not_business_accuracy','without_project':{'mode_and_fields_to_enter':3},'with_saved_project':{'project_selection':1},'cases':1}}
        args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        await browser.close();print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:7886/')
    parser.add_argument('--browser',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    asyncio.run(check(parser.parse_args()))
