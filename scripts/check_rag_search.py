"""R2 real-corpus interface/backend checks, NOT a new relevance benchmark."""
import argparse
from collections import Counter
from pathlib import Path
import time

from scripts.eval_rag_retrieval import open_backend, preflight, bindings, deny_network
from tcm.mineru_guards import field_body, reference_target
from tcm.rag_benchmark import write


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--release',type=Path,default=Path('var/data_releases/r1_20261030'))
    p.add_argument('--assets',type=Path,default=Path('var/rag_r2_v1'))
    p.add_argument('--model',type=Path,default=Path('var/evidence_assistant_v1/local_bge_small_zh'))
    p.add_argument('--verify-python',type=Path,default=Path('.venv/bin/python'))
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();attempts=[];deny_network(attempts)
    integrity=preflight(args.release,args.verify_python)
    search,backend,lexical=open_backend(args)
    report={'kind':'interface_and_backend_parity_not_relevance_evaluation','preflight':integrity,
            'bindings':bindings(args),'queries':[]}
    try:
        questions=['查找与受潮和虫害有关的贮藏说明，保留出处',
                   '在白术和黄芪条目中，找受潮和虫害相关说明']
        for question in questions:
            for method in ('bm25','dense','hybrid'):
                result=search.search(question,method)
                allowed=result['request']['names']
                assert result['hits'] and result['status']=='candidates'
                assert all(not h['scope'].startswith('appendix:') and (not allowed or h['name'] in allowed)
                           for h in result['hits'])
                if not allowed: assert len({h['entry_id'] for h in result['hits']})>1
                report['queries'].append(result)
        for question,status in [('炒白术的贮藏','clarification_required'),
                                ('只查白术独立的【注意】字段','missing_field')]:
            result=search.search(question,'dense')
            assert result['status']==status and not result['hits'] and result['encoding_ms']==0
            report['queries'].append(result)
        quarantine=[]
        by_id={d['source_record_id']:d for d in search.documents}
        for q in search.release['sidecars']['mapping']['quarantined_candidates']:
            doc=by_id[q['source_record_id']];name=doc['fields']['name_pinyin']['value']
            result=search.search('ETCM '+name+'的药用部位与药典'+q['index_name']+'来源段','exact')
            assert result['status']=='clarification_required' and not result['hits'] and not result['structured_hits']
            quarantine.append({'source_record_id':q['source_record_id'],'entry_id':q['entry_id'],
                               'status':result['status'],'reason':result['reason']})
        report['quarantine_checks']=quarantine
        # Same vector, same full research corpus. Numeric backend parity is
        # independent of the authored questions/answers and is not relevance.
        question=questions[0];vector=backend.encoder.encode([question],query=True)[0]
        allowed=[i for i,item in enumerate(backend.items) if not item['scope'].startswith('appendix:')
                 and field_body(search.rows[item['chunk_id']]['text'])
                 and not reference_target(search.rows[item['chunk_id']]['text'])]
        started=time.monotonic();q=backend.rank(vector,10,allowed);n=backend.dense.rank(vector,10,allowed)
        assert [i for i,_ in q]==[i for i,_ in n]
        delta=max(abs(a[1]-b[1]) for a,b in zip(q,n));assert delta<1e-5
        report['backend_parity']={'pool_size':len(allowed),'top_10_same_ids':True,
                                  'max_cosine_delta':delta,'latency_ms':round((time.monotonic()-started)*1000,3)}
        report['embedding']={'count':len(backend.items),'dimension':backend.encoder.dimension,
                             'truncated_documents':sum(bool(i['embedding_truncated']) for i in backend.items),
                             'truncated_by_label':dict(Counter(i['label'] for i in backend.items if i['embedding_truncated'])),
                             'truncated_by_scope':dict(Counter(i['scope'] for i in backend.items if i['embedding_truncated']))}
    finally:
        search.close()
        try:backend.close()
        finally:lexical.close()
    report.update(status='passed',network_attempts=len(attempts),external_calls=0,generation_calls=0)
    assert not attempts
    write(args.out,report)
    print({k:report[k] for k in ('status','backend_parity','embedding','network_attempts')})


if __name__=='__main__':main()
