"""Synthetic HTTP protocol fixture, not provider quality evaluation."""
import json
import httpx
from tcm.research_run_demo import synthetic_tool
from tcm.research_run_sdk import create_sdk
from tcm.research_run import run


class SyntheticProtocol:
    def __init__(self, failure=None):
        self.requests=[]
        self.failure=failure

    def __call__(self, request):
        body=json.loads(request.content)
        self.requests.append(body)
        if self.failure=='http':
            return httpx.Response(503,json={'error':{'message':'Synthetic unavailable','type':'server_error'}})
        observations=[json.loads(m['content']) for m in body['messages'] if m['role']=='tool']
        if not observations:
            name,args='search_disease_sources',{'name':'missing'}
        elif observations[-1]['status']=='empty':
            name,args='search_disease_sources',{'name':'Synthetic disease'}
        elif observations[-1]['tool']=='search_disease_sources':
            name,args='disease_gene_profile',{'name':observations[-1]['data']['items'][0]['record_id']}
        else:
            name,args='finish_research',{'status':'complete','evidence_ids':[o['evidence_id'] for o in observations if o['status']=='ok'],'unresolved':[]}
        arguments='{' if self.failure=='malformed' else json.dumps(args)
        message={'role':'assistant','content':None,'tool_calls':[{'id':'sdk-'+str(len(self.requests)),'type':'function','function':{'name':name,'arguments':arguments}}]}
        return httpx.Response(200,json={'id':'synthetic-response','object':'chat.completion','created':0,'model':'synthetic-sdk',
            'choices':[{'index':0,'message':message,'finish_reason':'tool_calls'}],
            'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120}})


async def evaluate():
    protocol=SyntheticProtocol()
    async with httpx.AsyncClient(transport=httpx.MockTransport(protocol)) as client:
        sdk=create_sdk(model='synthetic-sdk',api_key='synthetic-not-a-secret',base_url='https://synthetic.invalid/v1',http_client=client)
        result=await run('Find the synthetic disease, then read its target profile.',sdk,synthetic_tool)
    return {'mode':'real_sdk_synthetic_http','external_model_calls':0,
            'http_requests':len(protocol.requests),'usage_source':'synthetic_fixture',
            'provider_quality_evaluated':False,'run':result}
