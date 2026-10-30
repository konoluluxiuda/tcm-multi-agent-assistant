"""Production A loop: batches, dependencies, full pages and local API contracts."""
from collections import Counter
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock
from langchain_core.messages import AIMessage, ToolMessage
from tests.research_business_fixture import BusinessFixture
from tcm.research_run import run, Limits
from tcm.research_run_scope import RunScope
from tcm.research_run_local import DeclaredActionsDriver, LocalAnalysisTools, run_local_actions
from tcm.research_run_demo import synthetic_tool, SID
from tcm.research_run_durable import run_durable


def call(name, args, identifier):
    return {'name':name,'args':args,'id':identifier,'type':'tool_call'}


class Sequence:
    def __init__(self, replies):self.replies=iter(replies)
    def bind_tools(self,*args,**kwargs):return self
    async def ainvoke(self,messages):return AIMessage(content='',tool_calls=next(self.replies))


def dependency_scope():
    return RunScope.model_validate({'requirements':[
        {'id':'search','call':{'tool':'search_disease_sources','arguments':{'name':'Synthetic disease'}}},
        {'id':'profile','source_from':'search','all_pages':True,
         'call':{'tool':'disease_gene_profile','arguments':{}}}]})


class BusinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_five_real_tools_and_both_original_row_pages(self):
        with BusinessFixture() as f:
            before={p:p.read_bytes() for p in (f.diseases,f.herbs.base,f.herbs.database)}
            result=await run_local_actions(f.diseases,f.scope(),herbs=f.herbs.base,enrichment=f.herbs.database)
            self.assertEqual(result['status'],'complete',result)
            r=result['run']
            self.assertEqual((r['model_calls'],r['tool_calls']),(4,6))
            self.assertEqual({e['tool'] for e in r['events'][:-1]},
                             {'search_disease_sources','disease_gene_profile','rank_herbs_for_gene_set',
                              'herb_target_profile','herb_target_similarity'})
            pages=[p['data'] for p in r['evidence'].values() if p['tool']=='disease_gene_profile']
            self.assertEqual([len(p['rows']) for p in pages],[20,4])
            self.assertTrue(r['coverage']['complete'])
            self.assertTrue(all(p.read_bytes()==raw for p,raw in before.items()))

    async def test_four_independent_queries_are_admitted_and_paired(self):
        names=('one','two','three','four')
        scope=RunScope.model_validate({'requirements':[{'id':n,'call':{'tool':'search_disease_sources',
                      'arguments':{'name':n}}} for n in names]})
        class Model(Sequence):
            async def ainvoke(self,messages):
                tools=[m for m in messages if isinstance(m,ToolMessage)]
                if tools:
                    self.assertions=[m.tool_call_id for m in tools]
                    return AIMessage(content='',tool_calls=[call('finish_research',{'status':'complete',
                         'evidence_ids':[f'obs-{i}' for i in range(1,5)],'unresolved':[]},'finish')])
                return await super().ainvoke(messages)
        model=Model([[call('search_disease_sources',{'name':n},n) for n in names]])
        r=await run('q',model,synthetic_tool,scope=scope)
        self.assertEqual((r['status'],r['model_calls'],r['tool_calls']),('complete',2,4))
        self.assertEqual(model.assertions,list(names))

    async def test_invalid_member_budget_and_mixed_finish_block_entire_batch(self):
        for calls,limits,reason in (
            ([call('search_disease_sources',{'name':'one'},'a'),call('shell',{},'b')],Limits(),'unknown_tool'),
            ([call('search_disease_sources',{'name':'one'},'a'),call('search_disease_sources',{'name':'two'},'b')],Limits(tool_calls=1),'tool_budget'),
            ([call('search_disease_sources',{'name':'one'},'a'),call('finish_research',{'status':'complete','evidence_ids':[],'unresolved':[]},'b')],Limits(),'mixed_finish_batch'),
            ([call('search_disease_sources',{'name':'one'},'a'),call('search_disease_sources',{'name':'two'},'a')],Limits(),'duplicate_call_id'),
        ):
            backend=AsyncMock()
            r=await run('q',Sequence([calls]),backend,limits=limits)
            self.assertEqual(r['reason'],reason);backend.assert_not_awaited()

    async def test_forged_or_not_yet_discovered_source_is_blocked(self):
        for names in ((SID,),('etcm-disease:'+'b'*64,)):
            backend=AsyncMock()
            r=await run('q',Sequence([[call('disease_gene_profile',{'name':names[0]},'x')]]),backend,scope=dependency_scope())
            self.assertEqual(r['reason'],'out_of_scope');backend.assert_not_awaited()
        backend=AsyncMock(side_effect=synthetic_tool)
        r=await run('q',Sequence([
            [call('search_disease_sources',{'name':'Synthetic disease'},'s')],
            [call('disease_gene_profile',{'name':'etcm-disease:'+'b'*64},'p')]]),backend,scope=dependency_scope())
        self.assertEqual(r['reason'],'out_of_scope');self.assertEqual(backend.await_count,1)

    async def test_prefix_or_multiple_sources_do_not_unlock_analysis(self):
        for kind in ('prefix','ambiguous','empty'):
            async def backend(n,a):
                raw=await synthetic_tool(n,a)
                if kind=='prefix':raw['data']['items'][0]['match_kind']='prefix'
                elif kind=='ambiguous':
                    other=copy.deepcopy(raw['data']['items'][0]);other['record_id']='etcm-disease:'+'b'*64;other['source']['file_sha256']='b'*64
                    raw['data']['items'].append(other);raw['data']['total']=2
                else:raw['status']='empty';raw['data']['total']=0;raw['data']['items']=[]
                return raw
            s=dependency_scope();tracked=AsyncMock(side_effect=backend)
            r=await run('q',DeclaredActionsDriver(s),tracked,scope=s)
            self.assertEqual(r['status'],'partial');self.assertEqual(tracked.await_count,1)

    async def test_first_page_cannot_claim_all_rows_complete(self):
        scope=dependency_scope()
        async def backend(n,a):
            raw=await synthetic_tool(n,a)
            if n=='disease_gene_profile':raw['data']['has_next']=True
            return raw
        model=Sequence([[call('search_disease_sources',{'name':'Synthetic disease'},'s')],
            [call('disease_gene_profile',{'name':SID},'p')],
            [call('finish_research',{'status':'complete','evidence_ids':['obs-1','obs-2'],'unresolved':[]},'f')]])
        r=await run('q',model,backend,scope=scope)
        self.assertEqual(r['status'],'partial');self.assertFalse(r['coverage']['complete'])

    async def test_discovery_must_be_cited_to_complete_derived_requirement(self):
        model=Sequence([[call('search_disease_sources',{'name':'Synthetic disease'},'s')],
            [call('disease_gene_profile',{'name':SID},'p')],
            [call('finish_research',{'status':'complete','evidence_ids':['obs-2'],'unresolved':[]},'f')]])
        r=await run('q',model,synthetic_tool,scope=dependency_scope())
        self.assertEqual(r['status'],'partial')
        self.assertFalse(any(row['fulfilled'] for row in r['coverage']['requirements']))

    async def test_resume_in_middle_of_batch_does_not_repeat_saved_read(self):
        class Crash(BaseException):pass
        with tempfile.TemporaryDirectory() as tmp:
            calls=[call('search_disease_sources',{'name':n},n) for n in ('one','two','three','four')]
            model=Sequence([calls,[call('finish_research',{'status':'complete',
                'evidence_ids':['obs-1','obs-2','obs-3','obs-4'],'unresolved':[]},'f')]])
            backend=AsyncMock(side_effect=synthetic_tool)
            async def stop(s):
                if s['last_node']=='execute' and s['tool_calls']==1:raise Crash()
            options=dict(path=Path(tmp)/'journal.sqlite3',owner='alice',identifier='batch-resume-001',contract='synthetic-v1')
            with self.assertRaises(Crash):await run_durable('q',model,backend,on_state=stop,**options)
            r=await run_durable('q',model,backend,resume=True,**options)
            self.assertEqual((r['status'],r['model_calls'],r['tool_calls']),('partial',2,4))
            # No profile was requested by this unscoped test, so completion is
            # correctly partial, independently of successful batch recovery.
            self.assertEqual(Counter(c.args[1]['name'] for c in backend.await_args_list),Counter(('one','two','three','four')))

    async def test_dependency_is_reconstructed_from_durable_observations(self):
        class Crash(BaseException):pass
        with tempfile.TemporaryDirectory() as tmp:
            s=dependency_scope();backend=AsyncMock(side_effect=synthetic_tool)
            opts=dict(path=Path(tmp)/'j.sqlite3',owner='alice',identifier='dependency-resume',contract='synthetic-v1',scope=s)
            async def stop(snapshot):
                if snapshot['last_node']=='execute':raise Crash()
            with self.assertRaises(Crash):await run_durable('q',DeclaredActionsDriver(s),backend,on_state=stop,**opts)
            r=await run_durable('q',DeclaredActionsDriver(s),backend,resume=True,**opts)
            self.assertEqual(r['status'],'complete');self.assertEqual(backend.await_count,2)

    def test_bad_dependency_contracts_are_rejected(self):
        for parent in ('missing','profile'):
            raw=dependency_scope().model_dump();raw['requirements'][1]['source_from']=parent
            with self.assertRaises(ValueError):RunScope.model_validate(raw)
        raw=dependency_scope().model_dump();raw['requirements'][1]['call']['arguments']['name']=SID
        with self.assertRaises(ValueError):RunScope.model_validate(raw)

    async def test_batch_unknown_dispatch_stops_without_replaying_or_continuing(self):
        class Crash(BaseException):pass
        with tempfile.TemporaryDirectory() as tmp:
            count=Counter()
            async def backend(n,a):
                count[a['name']]+=1
                if a['name']=='two':raise Crash()
                return await synthetic_tool(n,a)
            model=Sequence([[call('search_disease_sources',{'name':n},n) for n in ('one','two','three')]])
            opts=dict(path=Path(tmp)/'journal.sqlite3',owner='alice',identifier='batch-unknown-001',contract='synthetic-v1')
            with self.assertRaises(Crash):await run_durable('q',model,backend,**opts)
            r=await run_durable('q',model,backend,resume=True,**opts)
            self.assertEqual(r['reason'],'uncertain_tool_dispatch')
            self.assertEqual(count,Counter({'one':1,'two':1}))
            self.assertEqual(list(r['evidence']),['obs-1'])

    async def test_changed_page_version_or_target_set_is_not_accepted(self):
        s=dependency_scope()
        for mutation in ('catalog_revision','target_ids'):
            async def backend(n,a):
                raw=await synthetic_tool(n,a)
                if n=='disease_gene_profile':
                    raw['data']['page']=a['page'];raw['data']['has_next']=a['page']==1
                    if a['page']==2:
                        if mutation=='catalog_revision':raw['data']['catalog_revision']='changed'
                        else:raw['data']['target_ids']=['Q12345']
                return raw
            r=await run('q',DeclaredActionsDriver(s),backend,scope=s,limits=Limits(model_calls=6))
            self.assertEqual(r['status'],'partial')
            self.assertEqual(r['evidence']['obs-3']['status'],'error')
            self.assertEqual(r['evidence']['obs-2']['data']['target_ids'],['P12345'])

    async def test_cannot_batch_search_and_its_unobserved_dependency(self):
        backend=AsyncMock()
        r=await run('q',Sequence([[call('search_disease_sources',{'name':'Synthetic disease'},'s'),
            call('disease_gene_profile',{'name':SID},'p')]]),backend,scope=dependency_scope())
        self.assertEqual(r['reason'],'out_of_scope');backend.assert_not_awaited()

    async def test_new_actions_api_real_tools_and_owner_isolation(self):
        import os
        import httpx
        from fastapi import FastAPI
        from unittest.mock import patch
        from tcm.research_run_api import create_router
        class Memory:
            def valid_cookie(self,value):return value in ('alice','bob')
        with BusinessFixture() as f, patch.dict(os.environ,{
                'ENABLE_RESEARCH_TASKS':'1','RESEARCH_RUN_DB':str(f.root/'runs.sqlite3')}):
            app=FastAPI();app.include_router(create_router(Memory,lambda:'chat'))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',cookies={'chat':'alice'}) as client:
                body={'scope':f.scope().model_dump()}
                response=await client.post('/api/research/runs/actions',json=body)
                self.assertEqual(response.status_code,200,response.text)
                result=response.json();self.assertEqual(result['status'],'complete')
                self.assertEqual(result['run']['tool_calls'],6)
                identifier=result['run_id']
                saved=(await client.get('/api/research/runs/'+identifier)).json()
                self.assertTrue(saved['result']['run']['coverage']['complete'])
                self.assertNotIn('messages',saved['snapshot'])
                self.assertEqual((await client.post('/api/research/runs/actions',json={**body,'database':'/tmp/other'})).status_code,422)
                self.assertEqual((await client.post('/api/research/runs/actions',json=body,headers={'Origin':'https://elsewhere.invalid'})).status_code,403)
                client.cookies.set('chat','bob')
                self.assertEqual((await client.get('/api/research/runs/'+identifier)).status_code,404)
                client.cookies.clear()
                self.assertEqual((await client.post('/api/research/runs/actions',json=body)).status_code,401)

    async def test_false_last_page_cannot_hide_remaining_original_rows(self):
        async def backend(n,a):
            raw=await synthetic_tool(n,a)
            if n=='disease_gene_profile':raw['data']['profile']['target_row_count']=24
            return raw
        s=dependency_scope()
        r=await run('q',DeclaredActionsDriver(s),backend,scope=s)
        self.assertEqual(r['status'],'partial')
        self.assertEqual(r['evidence']['obs-2']['status'],'error')

    async def test_actual_sdk_accepts_legal_batch_even_with_parallel_false(self):
        import httpx
        from tcm.research_run_sdk import create_sdk
        requests=[]
        def respond(request):
            body=json.loads(request.content);requests.append(body)
            self.assertIs(body['parallel_tool_calls'],False)
            tools=[m for m in body['messages'] if m['role']=='tool']
            selected=([call('search_disease_sources',{'name':n},n) for n in ('one','two')]
                if not tools else [call('finish_research',{'status':'complete',
                    'evidence_ids':['obs-1','obs-2'],'unresolved':[]},'f')])
            native=[{'id':c['id'],'type':'function','function':{'name':c['name'],
                     'arguments':json.dumps(c['args'])}} for c in selected]
            return httpx.Response(200,json={'id':'synthetic-response','object':'chat.completion','created':0,
                'model':'synthetic-sdk','choices':[{'index':0,'message':{'role':'assistant','content':None,
                    'tool_calls':native},'finish_reason':'tool_calls'}],
                'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120}})
        scope=RunScope.model_validate({'requirements':[{'id':n,'call':{'tool':'search_disease_sources',
                      'arguments':{'name':n}}} for n in ('one','two')]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            sdk=create_sdk(model='synthetic-sdk',api_key='synthetic-only',
                base_url='https://synthetic.invalid/v1',http_client=client)
            r=await run('q',sdk,synthetic_tool,scope=scope)
        self.assertEqual((r['status'],r['model_calls'],r['tool_calls']),('complete',2,2))
        self.assertEqual([m['tool_call_id'] for m in requests[1]['messages'] if m['role']=='tool'],['one','two'])
        self.assertEqual((r['input_tokens'],r['output_tokens']),(200,40))

    def test_malformed_derived_arguments_are_validation_errors(self):
        from pydantic import ValidationError
        for arguments in (None, [], 'invalid'):
            raw=dependency_scope().model_dump();raw['requirements'][1]['call']['arguments']=arguments
            with self.assertRaises(ValidationError):RunScope.model_validate(raw)
