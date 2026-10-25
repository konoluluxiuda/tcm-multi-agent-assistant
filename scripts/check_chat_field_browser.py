"""Actual local chat UI, source replay and frozen development query acceptance."""
import argparse
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright


async def check(args):
    root=Path(__file__).resolve().parents[1]
    suite=json.loads((root/'eval/retrieval_robustness_dev_v1.json').read_text())
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path=str(args.browser),headless=True,args=['--no-sandbox'])
        context=await browser.new_context();page=await context.new_page();errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        await page.goto(args.url)
        async def send(question):
            await page.locator('#message-input').fill(question)
            async with page.expect_response(lambda r:r.url.endswith('/chat') and r.request.method=='POST') as pending:
                await page.locator('#chat-form button[type=submit]').click()
            response=await pending.value
            assert response.status==200,(question,response.status)
            data=await response.json()
            assert data.get('research'),(question,list(data))
            return data['research']
        ui=[]
        for question,first in [('当归买回来放在哪里保存？','贮藏'),
                               ('整理当归的资料，想知道它主要用来做什么。','功能与主治'),
                               ('当归的功效和贮藏都需要，不过请先列保存要求。','贮藏')]:
            result=await send(question)
            assert result['status']=='candidates',result['status']
            assert result['tool_result']['hits'][0]['label']==first
            assert all(h['evidence'] for h in result['tool_result']['hits'])
            ui.append({'question':question,'first':first,'status':result['status']})
        before=[h['chunk_id'] for h in result['tool_result']['hits']]
        await page.reload()
        replay=await send('出处呢')
        assert [h['chunk_id'] for h in replay['tool_result']['hits']]==before
        rows=[]
        for case in suite['cases']:
            response=await context.request.post(args.url.rstrip('/')+'/chat',data={'query':case['question'],'answer_mode':'auto'})
            data=await response.json()
            result=data.get('research',{});hits=(result.get('tool_result') or {}).get('hits',[])
            ids=[h['chunk_id'] for h in hits]
            if case['expect_abstention']:
                passed=(response.status==403 and isinstance(data.get('detail'),str) and '此部署支持资料查询' in data['detail']) or (response.status==200 and result.get('status') in ('field_not_resolved','field_not_observed','no_match') and not hits)
            else:
                passed=(response.status==200 and result.get('status')=='candidates'
                        and set(case['expected_chunk_ids']).issubset(ids)
                        and (not case['primary_chunk_id'] or ids[0]==case['primary_chunk_id'])
                        and all(h['name']==case['herb'] and h['evidence'] for h in hits))
            rows.append({'id':case['id'],'http':response.status,'status':result.get('status'),'passed':bool(passed),'hit_ids':ids})
        report={'status':'passed' if all(r['passed'] for r in rows) and not errors else 'failed',
                'evaluation':'seen_development_cases_not_independent','ui':ui,'reload_source_replay':True,
                'total':len(rows),'passed':sum(r['passed'] for r in rows),'cases':rows,'javascript_errors':errors}
        args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        await browser.close()
        print(json.dumps({k:report[k] for k in ('status','total','passed')}))
        assert report['status']=='passed'


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--url',default='http://127.0.0.1:7883/')
    p.add_argument('--browser',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    asyncio.run(check(p.parse_args()))
