"""Two fixed synthetic tasks. Preview by default; live execution requires opt-in."""
import argparse
import asyncio
import json
import os
from pathlib import Path

for key in ('LANGCHAIN_TRACING','LANGCHAIN_TRACING_V2','LANGSMITH_TRACING'):
    os.environ[key]='false'

import httpx
from tcm.research_run import Limits, run
from tcm.research_run_demo import SID
from tcm.research_synthetic_v2 import NAME, VERSION, synthetic_tool
from tcm.research_run_sdk import create_sdk

TASKS = (
    {'id':'S01','question':'Find the source named Synthetic disease using search_disease_sources, then read its target profile. Finish citing the successful observations.',
     'required':('search_disease_sources','disease_gene_profile')},
    {'id':'S02','question':'Search for missing first. If empty, search for Synthetic disease, then read the returned source target profile. Finish citing successful observations.',
     'required':('search_disease_sources','disease_gene_profile')},
)


def preview():
    return {'mode':'preview','fixture_version':VERSION,'endpoint':'https://api.deepseek.com','model':'deepseek-flash',
            'tasks':list(TASKS),'max_http_requests':8,'max_requests_per_task':4,
            'max_output_tokens_per_request':650,'max_seconds_per_task':120,
            'data':'Fixed synthetic questions, static tool schemas and synthetic tool observations only',
            'retry':False,'real_corpus_access':False,'conversation_history':False}


class BudgetTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner):self.inner=inner;self.calls=0
    async def handle_async_request(self, request):
        if request.url.host!='api.deepseek.com' or request.url.scheme!='https' or request.url.path!='/chat/completions':
            raise ValueError('Unexpected live destination')
        if self.calls>=8:raise ValueError('HTTP request budget exhausted')
        self.calls+=1
        return await self.inner.handle_async_request(request)
    async def aclose(self):await self.inner.aclose()


async def evaluate(api_key, *, transport=None):
    transport=BudgetTransport(transport or httpx.AsyncHTTPTransport(retries=0))
    results=[]
    async with httpx.AsyncClient(transport=transport,follow_redirects=False,trust_env=False) as client:
        sdk=create_sdk(model='deepseek-flash',api_key=api_key,base_url='https://api.deepseek.com',
                       http_client=client,call_seconds=30.0)
        for task in TASKS:
            before=transport.calls
            result=await run(task['question'],sdk,synthetic_tool,required=task['required'],
                             limits=Limits(model_calls=4,tool_calls=3,seconds=120.0,call_seconds=30.0))
            events=result['events']
            tools=[e.get('tool') for e in events if e.get('tool')]
            profile_ok=any(e.get('tool')=='disease_gene_profile' and e.get('arguments',{}).get('name') in (SID,NAME) and e.get('status')=='ok' for e in events)
            branch_ok=(task['id']!='S02' or (len(events)>=3 and events[0].get('tool')=='search_disease_sources'
                       and events[0].get('arguments',{}).get('name')=='missing' and events[0].get('status')=='empty'
                       and events[1].get('arguments',{}).get('name')=='Synthetic disease'))
            search_ok=any(e.get('tool')=='search_disease_sources' and e.get('arguments',{}).get('name')=='Synthetic disease' and e.get('status')=='ok' for e in events)
            passed=search_ok and result['status']=='complete' and profile_ok and branch_ok and tools[0]=='search_disease_sources'
            results.append({'id':task['id'],'passed':passed,'http_requests':transport.calls-before,'run':result})
            # A provider/protocol failure is not a reason to burn the remaining budget.
            if result['reason'] in ('model_error','timeout','time_budget'):break
    return {'mode':'live_synthetic','fixture_version':VERSION,'http_requests':transport.calls,'planned_tasks':len(TASKS),
            'run_tasks':len(results),'passed':sum(r['passed'] for r in results),'results':results}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',help='Explicitly authorize this fixed two-task external run')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if not args.live:
        print(json.dumps(preview(),ensure_ascii=False,indent=2));return
    from dotenv import dotenv_values
    config={**dotenv_values(Path(__file__).resolve().parents[1]/'.env'),**os.environ}
    if config.get('CHAT_MODEL')!='deepseek-flash' or config.get('CHAT_BASE_URL','').rstrip('/')!='https://api.deepseek.com':
        parser.error('Configured endpoint/model differs from reviewed preview')
    key=config.get('CHAT_API_KEY')
    if not key:parser.error('Missing CHAT_API_KEY')
    if args.output and args.output.exists():parser.error('Output exists; use a new file to preserve audit history')
    result=asyncio.run(evaluate(key))
    raw=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('x') as f:f.write(raw+'\n')
    print(raw)
    if result['passed']!=len(TASKS):raise SystemExit(1)


if __name__=='__main__':main()
