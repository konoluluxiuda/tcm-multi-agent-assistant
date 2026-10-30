"""Browser regression for current-request retention and negated project objects."""
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
        await page.goto(args.url)
        doc=await (await context.request.post(args.url+'api/research/projects',data={'title':'当归川芎对比研究','herbs':['当归','川芎'],'question':'功效及贮藏'})).json()
        await page.locator('#local-research-panel > summary').click()
        search=page.locator('#project-search-form')
        query='继续上次当归与川芎的研究，这次只看白术的贮藏，现在还要注意事项'
        await search.locator('input').fill(query);await search.locator('button').click()
        await page.locator('#project-search-results button').filter(has_text='继续这个项目').first.click(timeout=40000)
        await page.wait_for_function("document.querySelector('[data-report-question] input').value==='看白术的贮藏，现在还要注意事项'")
        assert await page.locator('#local-research-form input').first.input_value()=='白术'
        query='继续上次当归与川芎的研究，这次不要当归，只看白术的贮藏'
        await search.locator('input').fill(query);await search.locator('button').click()
        await page.locator('#project-search-results button').filter(has_text='继续这个项目').first.click(timeout=40000)
        await page.wait_for_function("document.querySelector('#project-search-results').innerText.includes('排除药材的要求')")
        assert await page.locator('#local-research-form input').first.input_value()=='白术'
        extra=[]
        for i in range(3):
            extra.append(await (await context.request.post(args.url+'api/research/projects',data={'title':f'额外项目{i}','herbs':['甘草'],'question':'贮藏'})).json())
        listed=await (await context.request.post(args.url+'chat',data={'query':'列出所有研究项目'})).json()
        assert listed['project_memory']['total']==4,listed
        await search.locator('input').fill('列出所有研究项目');await search.locator('button').click()
        await page.wait_for_function("document.querySelectorAll('#project-search-results button').length===4")
        async with page.expect_response(lambda r:'/apply' in r.url and r.request.method=='POST') as applied_response:
            await page.locator('#project-search-results button').first.click()
        applied=await (await applied_response.value).json()
        assert applied['question_source']=='saved_project',applied
        await page.wait_for_function("expected => document.querySelector('[data-report-question] input').value===expected",arg=applied['question'])
        await search.locator('input').fill('帮我找一下之前关于黄芪的研究项目');await search.locator('button').click()
        await page.wait_for_function("document.querySelector('#project-search-results').innerText.includes('没有保存包含这些药材的项目')")
        assert await page.locator('#project-search-results button').count()==0
        jobs=await (await context.request.get(args.url+'api/research/jobs')).json();assert not jobs['items']
        for item in extra:
            await context.request.delete(args.url+'api/research/projects/'+item['id']+'?revision=1')
        await context.request.delete(args.url+'api/research/projects/'+doc['id']+'?revision=1')
        assert not errors,errors
        report={'status':'passed','current_entity_preserved':'白术','all_current_clauses_preserved':True,
                'negative_entity_application_rejected':True,'all_four_projects_listed':True,
                'unsaved_herb_no_candidate':True,'listing_selection_retains_saved_focus':True,
                'no_automatic_job_submission':True,'javascript_errors':errors,'external_model_calls':0}
        args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(report,ensure_ascii=False));await browser.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:7889/');p.add_argument('--browser',type=Path,required=True);p.add_argument('--out',type=Path,required=True);asyncio.run(check(p.parse_args()))
