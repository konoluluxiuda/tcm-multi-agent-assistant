"""Isolated synthetic API; never imports a provider factory or reads source databases."""
import json
from fastapi import FastAPI
from langchain_core.messages import AIMessage, ToolMessage
from tcm.research_run import run

SID='etcm-disease:'+'a'*64


async def synthetic_tool(name,args):
    if name=='search_disease_sources':
        items=[] if args['name']=='missing' else [{'record_id':SID,'name':'Synthetic disease','target_count':1,'capture_state':'synthetic','species_status':'synthetic','source':{'file_sha256':'a'*64},'match_kind':'exact'}]
        data={'query':args['name'],'dataset_version':'synthetic-v1','catalog_revision':'synthetic','total':len(items),'page':1,'has_next':False,'items':items}
        status='ok' if items else 'empty'
    else:
        if args['name']!=SID:raise ValueError('Synthetic source not found')
        data={'record_id':SID,'dataset_version':'synthetic-v1','catalog_revision':'synthetic','profile':{'name':'Synthetic disease','target_count':1,'source':{'file_sha256':'a'*64}},'target_ids':['P12345'],'rows':[],'page':1,'has_next':False,'identical_set_sources':1};status='ok'
    return {'tool':name,'status':status,'data':data,'elapsed_ms':0.0}


class OfflineModel:
    """Deterministic SDK substitute, not a live LLM evaluation."""
    def bind_tools(self,tools,**kwargs):return self
    async def ainvoke(self,messages):
        observations=[json.loads(m.content) for m in messages if isinstance(m,ToolMessage)]
        if not observations:name,args='search_disease_sources',{'name':'missing'}
        elif observations[-1]['status']=='empty':name,args='search_disease_sources',{'name':'Synthetic disease'}
        elif observations[-1]['tool']=='search_disease_sources':
            name,args='disease_gene_profile',{'name':observations[-1]['data']['items'][0]['record_id']}
        else:name,args='finish_research',{'status':'complete','evidence_ids':[o['evidence_id'] for o in observations if o['status']=='ok'],'unresolved':[]}
        return AIMessage(content='',tool_calls=[{'id':'call-'+str(len(observations)), 'name':name,'args':args,'type':'tool_call'}])


app=FastAPI(title='J01 synthetic execution demo')

@app.post('/synthetic-research/run')
async def demo():
    return {'mode':'synthetic_offline_model','completion_scope':'required_tool_observations_only',
            'run':await run('Find the synthetic disease, then read its target profile.',OfflineModel(),synthetic_tool)}
