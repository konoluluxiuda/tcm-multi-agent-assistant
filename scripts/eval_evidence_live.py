"""Run an explicitly authorized selection from the five pharmacopoeia development questions.

Questions and bounded local excerpts go to the configured DeepSeek endpoint.
No PubMed, ETCM, conversation history or entire documents are sent.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
CASE_IDS=('D01','D03','D04','D05','D09')


class BoundedModel:
    def __init__(self,model,question):
        self.model=model
        self.question=question
        self.calls=0
        self.transfers=[]

    def invoke(self,messages):
        payload=json.loads(messages[-1].content)
        if len(messages)!=2 or payload.get('question')!=self.question or self.calls>=2:
            raise ValueError('Authorized per-question model budget exceeded')
        evidence=payload.get('evidence',[])
        if len(evidence)>6 or any(e['source_group']!='traditional' or len(e['text'])>800 for e in evidence):
            raise ValueError('Authorized excerpt scope exceeded')
        allowed={'question','evidence','tool_statuses'} if 'evidence' in payload else {'question','data_mode','available_tools'}
        if set(payload)!=allowed:
            raise ValueError('Unexpected model payload fields')
        self.calls+=1
        self.transfers.append({'call':self.calls,'excerpts':len(evidence),
                               'excerpt_characters':[len(e['text']) for e in evidence]})
        return self.model.invoke(messages)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--cases',nargs='+',choices=CASE_IDS,help='Select only cases covered by the current run authorization')
    args=parser.parse_args()
    if args.cases and len(set(args.cases))!=len(args.cases):parser.error('Duplicate case IDs are not permitted')
    from dotenv import load_dotenv
    load_dotenv()
    os.environ.update(ENABLE_PHARMACOPOEIA_RESEARCH='1',ENABLE_PUBMED='0',ENABLE_NATURAL_ROUTING='1',
                      LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false')
    endpoint=urlsplit(os.environ['CHAT_BASE_URL'])
    if endpoint.scheme!='https' or endpoint.hostname!='api.deepseek.com' or endpoint.port not in (None,443) or endpoint.username or endpoint.password:
        raise ValueError('This authorization applies only to https://api.deepseek.com')
    if os.environ['CHAT_MODEL']!='deepseek-flash':
        raise ValueError('This authorization applies only to deepseek-flash')
    from config import create_chat_model
    from tcm.agent_research import runtime
    from tcm.assistant_router import resolve
    from tcm.mineru_chat import ResearchTool
    from tcm.evidence_assistant import EvidenceAssistant
    prepared=json.loads((ROOT/'var/evidence_assistant_v1/real_task_preparation/batch_01.json').read_text())
    if tuple(c['id'] for c in prepared['cases'])!=CASE_IDS:
        raise ValueError('Authorized case selection changed')
    service=runtime.get_service()
    if service.receipt['index_sha256']!=prepared['index_sha256']:
        raise ValueError('Source snapshot differs from prepared batch')
    suite=json.loads((ROOT/'eval/evidence_assistant_dev_v1.json').read_text())
    original={c['id']:c['question'] for c in suite['cases']}
    if any(c['question']!=original[c['id']] for c in prepared['cases']):
        raise ValueError('Authorized question changed')
    selected_cases=[c for c in prepared['cases'] if not args.cases or c['id'] in args.cases]
    args.out.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    # Reserve artifact before any paid calls. An existing output never triggers a rerun.
    fd=os.open(args.out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    report={'kind':'bounded_selected_question_development_run','selected_case_ids':[c['id'] for c in selected_cases],
            'model':'deepseek-flash','endpoint_host':endpoint.hostname,
            'data_mode':'bounded_pharmacopoeia_only','index_sha256':prepared['index_sha256'],
            'source_code_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in (
                'tcm/evidence_assistant.py','tcm/assistant_router.py','tcm/mineru_chat.py','scripts/eval_evidence_live.py')},
            'human_review':'pending','business_accuracy':None,'pubmed_calls':0,'embedding_calls':0,'cases':[]}
    def local(query):return service.search(query,method='name_field',k=3)
    def disabled_literature(query):return {'status':'disabled','articles':[]}
    with os.fdopen(fd,'w') as output:
        for case in selected_cases:
            start=time.monotonic()
            selected=resolve(case['question'])
            if selected and selected['tool']=='research':
                baseline,_=ResearchTool(service).execute(selected['request']['question'],None)
            else:
                baseline={'status':'unhandled' if not selected else selected['request'].get('topic','unsupported'),
                          'message':'Current rules did not produce a pharmacopoeia lookup.','tool_result':None}
            baseline={'route':selected,'result':baseline,'model_calls':0,'duration_ms':round((time.monotonic()-start)*1000,2)}
            model=BoundedModel(create_chat_model(temperature=0,timeout=30,max_retries=0,max_tokens=1800),case['question'])
            result=EvidenceAssistant(model=model,local_tool=local,literature_tool=disabled_literature).run(case['question'])
            row={'id':case['id'],'question':case['question'],'acceptance':case['acceptance'],
                 'rule_baseline':baseline,'result':result,'transfers':model.transfers,
                 'semantic_review':{'status':'pending','task_completion':None,'citation_support':None}}
            report['cases'].append(row)
            output.seek(0);json.dump(report,output,ensure_ascii=False,indent=2);output.truncate();output.flush()
            print(json.dumps({'id':case['id'],'rule_status':baseline['result']['status'],
                              **{k:result[k] for k in ('status','model_calls','duration_ms','input_tokens','output_tokens')}},ensure_ascii=False),flush=True)
        report['summary']={'total':len(report['cases']),
                           'model_calls':sum(c['result']['model_calls'] for c in report['cases']),
                           'transferred_excerpts':sum(t['excerpts'] for c in report['cases'] for t in c['transfers']),
                           'cost':None,'task_completion':None}
        output.seek(0);json.dump(report,output,ensure_ascii=False,indent=2);output.truncate()
    print(json.dumps(report['summary'],ensure_ascii=False))


if __name__=='__main__':main()
