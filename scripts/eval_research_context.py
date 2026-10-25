"""Offline context projection comparison; no LLM or provider-token measurement."""
import json
from langchain_core.messages import SystemMessage,HumanMessage,AIMessage,ToolMessage
from tcm.research_context import ContextPolicy,project
from tcm.research_token_budget import TokenEstimator
from tcm.research_run import schemas


def evaluate():
    messages=[SystemMessage(content='Preserve source IDs and user requirements.'),HumanMessage(content='Compare synthetic sources and keep original record locations.')]
    estimator=TokenEstimator();totals={'full':0,'projected':0};checks=[]
    for i in range(6):
        messages.append(AIMessage(content='',tool_calls=[{'id':str(i),'name':'disease_gene_profile','args':{'name':'synthetic-'+str(i)}}]))
        messages.append(ToolMessage(tool_call_id=str(i),content=json.dumps({'evidence_id':'obs-'+str(i+1),'tool':'disease_gene_profile','status':'ok','data':{'record_id':'synthetic-'+str(i),'rows':[{'text':'synthetic detail '*700}],'target_ids':['P12345']}})))
        projected,check=project(messages,schemas(),ContextPolicy(max_bytes=40000,reserve_bytes=2000,keep_recent_tools=2))
        totals['full']+=estimator.count(messages,schemas());totals['projected']+=estimator.count(projected,schemas())
        assert projected[:2]==messages[:2]
        assert all(projected[n].tool_calls[0]['id']==projected[n+1].tool_call_id for n in range(2,len(projected),2))
        checks.append(check)
    return {'mode':'offline_projection_comparison','estimator':estimator.label,'external_model_calls':0,
            'six_projected_requests':totals,'all_projected_fit':all(c['fits'] for c in checks),
            'reduction_fraction':1-totals['projected']/totals['full'],'checks':checks,
            'limitation':'Synthetic prompt sizes only; not provider token savings or task completion accuracy.'}


if __name__=='__main__':print(json.dumps(evaluate(),ensure_ascii=False,indent=2))
