"""Small local same-candidate ranking diagnostic, reusing six known development questions."""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


def write(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def run(args):
    if args.out.exists():raise FileExistsError('Use a fresh directory')
    args.out.mkdir(parents=True)
    os.environ.update(LANGSMITH_TRACING='false',LANGCHAIN_TRACING_V2='false',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
    from tcm.mineru_pharmacopoeia import digest as fingerprint
    from tcm.mineru_retrieval import ResearchRetriever
    from tcm.local_dense import LocalEncoder,DenseRetriever
    from tcm.local_reranker import LocalReranker
    from tcm.cross_source_report import fuse
    questions=ROOT/'eval/reranker_priority_dev_v1.json'
    suite=json.loads(questions.read_text());cases=suite['cases'][:6]
    attempts=[]
    def deny(*a,**kw):attempts.append('blocked');raise RuntimeError('No network permitted')
    with ExitStack() as stack:
        for obj,key in ((socket.socket,'connect'),(socket.socket,'connect_ex'),(socket.socket,'sendto'),(socket,'getaddrinfo')):
            stack.enter_context(patch.object(obj,key,deny))
        lexical=ResearchRetriever(ROOT/'var/mineru_retrieval_v2');stack.callback(lexical.close)
        assert lexical.receipt['view_sha256']==suite['source_view_sha256']
        pools={}
        for case in cases:
            # Fixed entity+field pool, frozen before scoring. Relevance never selects the pool.
            gate=lexical.query(case['herb']+'的功效和贮藏',method='name_field',k=20)
            assert gate['status']=='candidates' and 1<len(gate['hits'])<=20
            assert case['expected_chunk_id'] in {h['chunk_id'] for h in gate['hits']}
            pools[case['id']]=gate['hits']
        write(args.out/'protocol.json',{'kind':'same_pool_known_development_diagnostic',
            'questions_sha256':fingerprint(questions),'source_view_sha256':lexical.receipt['view_sha256'],
            'case_ids':[c['id'] for c in cases],
            'candidate_ids':{k:[h['chunk_id'] for h in v] for k,v in pools.items()},
            'external_model_calls':0,'frozen_before_scoring':True,
            'not_new_holdout':True,'retrieval_coverage_not_evaluated':True,
            'interpretation':'Rank ordering within a fixed entity/field pool; no full-corpus recall claim.'})
        started=time.perf_counter()
        encoder=LocalEncoder(ROOT/'var/evidence_assistant_v1/local_bge_small_zh',threads=2)
        dense=DenseRetriever(lexical,encoder,ROOT/'var/evidence_assistant_v1/local_dense_cache')
        reranker=LocalReranker(ROOT/'var/local_reranker_v1/model',threads=2)
        load_seconds=time.perf_counter()-started;rows=[]
        by_chunk={key:i for i,key in enumerate(dense.identifiers)}
        for case in cases:
            pool=pools[case['id']];allowed={h['chunk_id'] for h in pool};by_id={h['chunk_id']:h for h in pool}
            q=case['question'];start=time.perf_counter()
            words=lexical.query(q,method='name_bm25',k=20)['hits']
            words=[h for h in words if h['chunk_id'] in allowed]
            # Fill absent lexical ties deterministically, preserving the same pool in every method.
            words += [by_id[key] for key in sorted(allowed-{h['chunk_id'] for h in words})]
            lexical_ms=(time.perf_counter()-start)*1000
            start=time.perf_counter();vector=encoder.encode([q],query=True)[0]
            ranked=dense.rank(vector,len(pool),[by_chunk[h['chunk_id']] for h in pool])
            dense_hits=[by_id[dense.identifiers[i]] for i,_ in ranked]
            hybrid=fuse([words,dense_hits,pool],k=len(pool));hybrid_ms=lexical_ms+(time.perf_counter()-start)*1000
            start=time.perf_counter();reranked,meta=reranker.rerank(q,hybrid,k=len(pool))
            rerank_ms=hybrid_ms+(time.perf_counter()-start)*1000
            focus=q.rsplit('重点是',1)[-1];label={'功效':'功能与主治','贮藏':'贮藏'}.get(focus)
            rule=sorted(hybrid,key=lambda h:h['label']!=label)
            for method,hits,elapsed in (('lexical',words,lexical_ms),('hybrid',hybrid,hybrid_ms),
                                         ('hybrid_rerank',reranked,rerank_ms),('hybrid_literal_priority',rule,hybrid_ms)):
                assert {h['chunk_id'] for h in hits}==allowed
                for hit in hits:
                    assert all(hit[k]==by_id[hit['chunk_id']][k] for k in ('text','evidence','label','scope','entry_id'))
                ids=[h['chunk_id'] for h in hits];rank=ids.index(case['expected_chunk_id'])+1
                rows.append({'id':case['id'],'method':method,'candidate_count':len(pool),'hit_ids':ids,
                    'target_rank':rank,'hit_at_1':rank==1,'rr':1/rank,'ms':elapsed})
        assert not attempts,attempts
    summary={method:{'total':len([r for r in rows if r['method']==method]),
        'hit_at_1':sum(r['hit_at_1'] for r in rows if r['method']==method),
        'mrr':sum(r['rr'] for r in rows if r['method']==method)/len(cases),
        'mean_ms':sum(r['ms'] for r in rows if r['method']==method)/len(cases)}
        for method in ('lexical','hybrid','hybrid_rerank','hybrid_literal_priority')}
    report={'status':'passed','kind':'known_development_same_pool_ranking_only','summary':summary,'cases':rows,
        'load_seconds':load_seconds,'external_model_calls':0,'network_attempts':len(attempts),'source_witnesses_unchanged':True,
        'encoder_fingerprint':encoder.fingerprint,'reranker_fingerprint':reranker.fingerprint,
        'retrieval_recall':None,'business_accuracy':None,'independent_holdout':False,
        'decision':'Exact DataOps fields keep deterministic lookup; this diagnostic alone does not justify enabling reranking.'}
    write(args.out/'summary.json',report);print(json.dumps({k:report[k] for k in ('status','summary','network_attempts')},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    run(p.parse_args())
