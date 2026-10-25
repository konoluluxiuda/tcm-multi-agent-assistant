import json
import unittest
from langchain_core.messages import AIMessage,ToolMessage
from tcm.research_evidence_read import read,ReadEvidence
from tcm.research_run import run,Limits
from tcm.research_context import ContextPolicy
from tcm.research_run_demo import SID,synthetic_tool


class ReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_pages_bind_to_original_and_do_not_modify_it(self):
        source=await synthetic_tool('disease_gene_profile',{'name':SID})
        source['data']['target_ids']=['P'+str(i) for i in range(7)]
        evidence={'obs-1':source};before=json.dumps(evidence)
        first=read(evidence,{'evidence_id':'obs-1','field':'target_ids','limit':3})['data']
        self.assertEqual(first['items'],['P0','P1','P2']);self.assertEqual(first['next_offset'],3)
        last=read(evidence,{'evidence_id':'obs-1','field':'target_ids','offset':6})['data']
        self.assertEqual(last['items'],['P6']);self.assertIsNone(last['next_offset'])
        self.assertEqual(first['source_binding']['record_id'],SID)
        self.assertEqual(json.dumps(evidence),before)

    async def test_no_cross_run_recursive_or_missing_field_access(self):
        source=await synthetic_tool('disease_gene_profile',{'name':SID})
        for evidence,field,code in (({},'rows','unknown_source_evidence'),
            ({'obs-1':{'tool':'read_run_evidence','status':'ok'}},'rows','unknown_source_evidence'),
            ({'obs-1':source},'items','field_not_available')):
            self.assertEqual(read(evidence,{'evidence_id':'obs-1','field':field})['error']['code'],code)
        for args in ({'evidence_id':'other-run/obs-1','field':'rows'}, {'evidence_id':'obs-1','field':'rows','path':'/tmp'},
                     {'evidence_id':'obs-1','field':'rows','limit':21}):
            with self.assertRaises(ValueError):ReadEvidence.model_validate(args)

    async def test_large_item_is_explicit_error_and_byte_limited_page_has_cursor(self):
        source=await synthetic_tool('disease_gene_profile',{'name':SID})
        source['data']['rows']=[{'text':'x'*3500}]*3
        r=read({'obs-1':source},{'evidence_id':'obs-1','field':'rows'})
        self.assertEqual(len(r['data']['items']),1);self.assertEqual(r['data']['next_offset'],1)
        source['data']['rows']=[{'text':'x'*7000}]
        self.assertEqual(read({'obs-1':source},{'evidence_id':'obs-1','field':'rows'})['error']['code'],'item_exceeds_read_budget')

    async def test_compact_then_read_then_finish_without_backend_requery(self):
        backend_calls=[];seen_read=[]
        async def backend(name,args):
            backend_calls.append(name);r=await synthetic_tool(name,args)
            if name=='disease_gene_profile':r['data']['rows']=[{'padding':'x'*12000}]
            else:r['data']['items'][0]['source']['padding']='y'*12000
            return r
        class Driver:
            def bind_tools(self,*a,**kw):return self
            async def ainvoke(self,messages):
                observations=[json.loads(m.content) for m in messages if isinstance(m,ToolMessage)]
                n=len(observations)
                if n==0:name,args='disease_gene_profile',{'name':SID}
                elif n==1:name,args='search_disease_sources',{'name':'Synthetic disease'}
                elif n==2:
                    seen_read.append(observations[0].get('compacted'))
                    name,args='read_run_evidence',{'evidence_id':'obs-1','field':'target_ids'}
                else:
                    seen_read.append(observations[-1]['data']['items'])
                    name,args='finish_research',{'status':'complete','evidence_ids':['obs-1','obs-2'],'unresolved':[]}
                return AIMessage(content='',tool_calls=[{'id':str(n),'name':name,'args':args}])
        r=await run('Read synthetic sources',Driver(),backend,
                    context_policy=ContextPolicy(max_bytes=26000,reserve_bytes=1000,keep_recent_tools=1))
        self.assertEqual(r['status'],'complete');self.assertEqual(seen_read,[True,['P12345']])
        self.assertEqual(backend_calls,['disease_gene_profile','search_disease_sources'])
        self.assertEqual(r['tool_calls'],3)
        self.assertEqual(r['evidence']['obs-3']['data']['source_evidence_id'],'obs-1')

    async def test_read_copy_cannot_replace_scope_citation_and_counts_against_budget(self):
        class Driver:
            def bind_tools(self,*a,**kw):return self
            async def ainvoke(self,messages):
                n=sum(isinstance(m,ToolMessage) for m in messages)
                actions=[('disease_gene_profile',{'name':SID}),
                         ('read_run_evidence',{'evidence_id':'obs-1','field':'target_ids'}),
                         ('finish_research',{'status':'complete','evidence_ids':['obs-2'],'unresolved':[]})]
                name,args=actions[min(n,2)]
                return AIMessage(content='',tool_calls=[{'id':str(n),'name':name,'args':args}])
        scope={'requirements':[{'id':'profile','call':{'tool':'disease_gene_profile','arguments':{'name':SID}}}]}
        r=await run('q',Driver(),synthetic_tool,scope=scope)
        self.assertEqual(r['status'],'partial');self.assertFalse(r['coverage']['complete'])
        limited=await run('q',Driver(),synthetic_tool,scope=scope,limits=Limits(tool_calls=1))
        self.assertEqual(limited['reason'],'tool_budget');self.assertEqual(limited['tool_calls'],1)
