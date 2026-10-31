"""Freeze evidence and questions before one real RAG comparison; preserve failures."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil

import httpx

from tcm.data_release import check
from tcm.mineru_pharmacopoeia import digest
from tcm.rag_benchmark import read,write
from tcm.rag_pipeline import RagPipeline,assemble,payload,LIMITS
from tcm.rag_model import BoundedChat,request_body
from tcm.rag_retrieval import ReleaseSearch
from tcm.rag_retrieval_eval import anchor_in_hit

ROOT=Path(__file__).resolve().parents[1]
SUITE=ROOT/'eval/rag_generation_v1.json'
CODE=('tcm/rag_pipeline.py','tcm/rag_retrieval.py','tcm/rag_model.py','tcm/rag_retrieval_eval.py',
      'tcm/rag_api.py','tcm/tracing.py','tcm/mineru_retrieval.py','tcm/mineru_guards.py','scripts/eval_rag_pipeline.py')
ENDPOINT='https://api.deepseek.com/chat/completions'
MODEL='deepseek-flash'


class FrozenSearch:
    def __init__(self,question,result):self.question,self.result=question,result
    def search(self,question,**kwargs):
        if question!=self.question:raise ValueError('Frozen query changed')
        return self.result


def code_hashes():return {n:digest(ROOT/n) for n in CODE}


def grade(case,result):
    message=result['message'];requested=case['compose'] and case['id'].startswith('G')
    shared=any(f['kind']=='shared_phrase' for f in (result.get('draft') or {}).get('findings',[]))
    terms=all(t in message for t in case['terms'])
    expected=case.get('expected_status','composed')
    return {'id':case['id'],'status':result['status'],'status_correct':result['status']==expected,
            'required_literal_terms_present':terms,'shared_phrase_reported':shared,
            'requested_grouping_met':not case.get('require_shared_phrase') or shared,
            'citation_and_literal_support':result['validation']['citation_and_literal_support'],
            'passed':result['status']==expected and terms and (not case.get('require_shared_phrase') or shared),
            'requests':result['stats']['requests'],'outcome':result['stats']['outcome'],
            'usage':result['stats']['usage'],'duration_ms':result['duration_ms'],
            'answer_chars':len(message),'free_paraphrase_semantics_verified':False}


def prepare(release,out):
    integrity=check(release)
    out.mkdir(parents=True,exist_ok=False);(out/'prepared').mkdir()
    ref=read(release/'benchmark_references.json');refs={r['case_id']:r for r in ref['cases']}
    registry=read(release/'sources.private.json')
    blocks={b['id']:b for b in read(Path(registry['config']['base'])/'blocks.json')}
    search=ReleaseSearch(release);cases=read(SUITE)['cases'];prepared=[]
    try:
        for case in cases:
            retrieval=search.search(case['question'],'bm25',k=6)
            bundle=assemble(retrieval)
            if case['id'].startswith('G') or case['id'] in ('C01','C02'):
                for rid in case['references']:
                    for anchor in refs[rid]['relevant_anchors']:
                        # Frozen raw source references are scorer-only; no IDs,
                        # fields or answers from these references enter search.
                        assert any(anchor_in_hit(anchor,h,blocks) for h in
                                   retrieval['hits']+retrieval['structured_hits']), (case['id'],anchor['anchor_id'])
                assert bundle['sources'] and not bundle['omitted_sources'] and not any(s['truncated'] for s in bundle['sources'])
            original=RagPipeline(FrozenSearch(case['question'],retrieval)).run(case['question'],False)
            write(out/'prepared'/(case['id']+'_retrieval.json'),retrieval)
            write(out/'prepared'/(case['id']+'_original.json'),original)
            body=httpx.Request('POST',ENDPOINT,json=request_body(payload(case['question'],bundle),MODEL)).content
            assert len(body)<=LIMITS['max_request_bytes']
            prepared.append({'id':case['id'],'request_body_sha256':hashlib.sha256(body).hexdigest(),
                             'request_bytes':len(body),'sources':len(bundle['sources']),
                             'retrieval_status':retrieval['status'],'generation_planned':case['id'].startswith('G')})
    finally:search.close()
    for n in CODE:
        dst=out/'frozen_source'/n;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/n,dst)
    protocol={'version':'rag-generation-live-v1','created_at':datetime.now(timezone.utc).isoformat(),
              'endpoint':ENDPOINT,'model':MODEL,'max_requests_total':4,'per_case_requests':1,
              'limits':LIMITS,'suite_sha256':digest(SUITE),'code_sha256':code_hashes(),
              'release_content_id':integrity['content_id'],'release_sha256':digest(release/'release.json'),
              'prepared':prepared,'prepared_sha256':{p.name:digest(p) for p in (out/'prepared').iterdir()},
              'reference_status':'exposed_R1_R2_sources; author_development_not_blind; pending_human_signoff',
              'data_sent':'current question + at most 6 source excerpts, each <=800 chars, total <=4000; no PDF/DB/history',
              'stop':'HTTP error or unknown outcome stops subsequent provider calls; no retry or schema correction'}
    write(out/'protocol.json',protocol);write(out/'integrity.json',integrity)
    return protocol


def execute(release,out,langfuse=False):
    protocol=read(out/'protocol.json')
    if (protocol['code_sha256']!=code_hashes() or protocol['suite_sha256']!=digest(SUITE) or
            protocol['release_sha256']!=digest(release/'release.json')):raise ValueError('Frozen inputs changed')
    for name,sha in protocol['prepared_sha256'].items():
        if digest(out/'prepared'/name)!=sha:raise ValueError('Frozen evidence changed')
    check(release)
    write(out/'execution_started.json',{'status':'consumed_even_if_interrupted','started_at':datetime.now(timezone.utc).isoformat()})
    from dotenv import dotenv_values
    conf=dotenv_values(ROOT/'.env')
    key=os.getenv('RAG_API_KEY') or conf.get('RAG_API_KEY') or conf.get('CHAT_API_KEY')
    if not key:raise ValueError('Missing configured generation key')
    os.environ.update(RAG_API_KEY=key,ENABLE_LANGFUSE='0',LANGFUSE_TRACING_ENABLED='false',
                      LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false')
    if langfuse:
        lf=dotenv_values(ROOT/'var/langfuse_local/app.env')
        for k in ('LANGFUSE_BASE_URL','LANGFUSE_PUBLIC_KEY','LANGFUSE_SECRET_KEY','TCM_TRACE_CONTENT'):
            if lf.get(k):os.environ[k]=lf[k]
        os.environ.update(ENABLE_LANGFUSE='1',LANGFUSE_TRACING_ENABLED='true')
    records=[];stopped=False;actual=0
    by_id={r['id']:r for r in protocol['prepared']}
    for case in read(SUITE)['cases']:
        if stopped and case['id'].startswith('G'):
            records.append({'id':case['id'],'status':'not_run_after_provider_error','requests':0,'passed':False});continue
        write(out/(case['id']+'_started.json'),{'status':'started_once','provider_budget':1 if case['id'].startswith('G') else 0})
        retrieval=read(out/'prepared'/(case['id']+'_retrieval.json'))
        model=BoundedChat(endpoint=ENDPOINT,model=MODEL,key=key,expected_sha=by_id[case['id']]['request_body_sha256']) if case['id'].startswith('G') else None
        result=RagPipeline(FrozenSearch(case['question'],retrieval),model).run(case['question'],case['compose'],
                      trace_metadata={'evaluation':'rag_r3_v1','case_id':case['id']})
        write(out/(case['id']+'_answer.json'),result)
        metric=grade(case,result);records.append(metric)
        actual+=metric['requests'];assert actual<=4
        if metric['outcome'] not in ('http_200','not_dispatched'):stopped=True
        write(out/(case['id']+'_metrics.json'),metric)
        print(json.dumps(metric,ensure_ascii=False),flush=True)
    from tcm.tracing import flush
    flush()
    report={'scope':'author_development_known_sources; no independent accuracy claim',
            'results':records,'actual_provider_requests':actual,'retries':0,'corrections':0,
            'passed':sum(r['passed'] for r in records),'total':len(records),
            'provider_stopped':stopped,'free_paraphrase_semantics_verified':False,'cost':None,
            'baseline':'same prepared evidence; original excerpts with zero generation; all originals retained'}
    write(out/'summary.json',report)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('prepare','execute'))
    p.add_argument('--release',type=Path,default=ROOT/'var/data_releases/r1_20261030')
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--with-langfuse',action='store_true')
    args=p.parse_args()
    report=prepare(args.release,args.out) if args.mode=='prepare' else execute(args.release,args.out,args.with_langfuse)
    print(json.dumps({k:report[k] for k in ('version','max_requests_total','actual_provider_requests','passed','total') if k in report},ensure_ascii=False))


if __name__=='__main__':main()
