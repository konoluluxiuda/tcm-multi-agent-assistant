"""Evaluate local ONNX dense retrieval alongside lexical baselines; network is blocked."""
import argparse
from collections import Counter
from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import time
from unittest.mock import patch

from scripts.eval_mineru_retrieval import anchor_matches
from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_retrieval import ResearchRetriever
from tcm.local_dense import LocalEncoder,DenseRetriever,build_vectors,RECIPE

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',type=Path,default=ROOT/'var/evidence_assistant_v1/local_bge_small_zh')
    parser.add_argument('--index',type=Path,default=ROOT/'var/mineru_retrieval_v2')
    parser.add_argument('--cache',type=Path,default=ROOT/'var/evidence_assistant_v1/local_dense_cache')
    parser.add_argument('--build-cache',action='store_true')
    parser.add_argument('--threads',type=int,default=4)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    suites=[('existing_regression','eval/mineru_retrieval_questions_v1.json','eval/mineru_retrieval_reference_v2.json'),
            ('body_diagnostic','eval/scoped_retrieval_dev_v1.json','eval/scoped_retrieval_reference_v1.json'),
            ('semantic_development','eval/dense_semantic_dev_v1.json','eval/dense_semantic_reference_v1.json')]
    if args.out.exists():raise FileExistsError('Use a fresh output directory')
    if not 1<=args.threads<=32:raise ValueError('threads must be 1–32')
    questions=[];refs={};suite_hashes={};reference_views=[]
    for label,qpath,rpath in suites:
        qp=ROOT/qpath;rp=ROOT/rpath;q=json.loads(qp.read_text());r=json.loads(rp.read_text())
        if r['question_sha256']!=digest(qp):raise ValueError('Question/reference mismatch')
        if r.get('source_view_sha256'):reference_views.append(r['source_view_sha256'])
        for case in q['cases']:questions.append({**case,'suite':label})
        for ref in r['references']:refs[(label,ref['case_id'])]=ref
        suite_hashes[label]={'questions_sha256':digest(qp),'reference_sha256':digest(rp)}
    args.out.mkdir(parents=True,mode=0o700)
    attempts=[]
    def deny(*a,**kw):attempts.append('blocked');raise RuntimeError('Network disabled during local dense experiment')
    with ExitStack() as stack:
        for obj,name in [(socket.socket,'connect'),(socket.socket,'connect_ex'),(socket.socket,'sendto'),(socket,'getaddrinfo')]:
            stack.enter_context(patch.object(obj,name,deny))
        load_started=time.monotonic();encoder=LocalEncoder(args.model,threads=args.threads)
        load_ms=round((time.monotonic()-load_started)*1000,3)
        lexical=ResearchRetriever(args.index);stack.callback(lexical.close)
        if any(v!=lexical.receipt['view_sha256'] for v in reference_views):raise ValueError('Reference source view mismatch')
        if args.build_cache:build_vectors(lexical,encoder,args.cache)
        dense=DenseRetriever(lexical,encoder,args.cache)
        encoder.encode(['本地模型预热'],query=True)
        records=[];raw=[]
        for method in ('bm25','name_field','name_bm25','dense','name_dense'):
            for case in questions:
                started=time.monotonic()
                result=(dense.query(case['question'],method=method,k=5) if method in ('dense','name_dense')
                        else lexical.query(case['question'],method=method,k=5))
                elapsed=(time.monotonic()-started)*1000
                row={'id':case['id'],'suite':case['suite'],'group':case['group'],'method':method,
                     'status':result['status'],'query_ms':round(elapsed,3),
                     'query_encoding_ms':result.get('query_encoding_ms',0),
                     'hit_ids':[h['chunk_id'] for h in result['hits']]}
                if case['group']=='abstention':
                    row['correct_abstention']=not result['hits'] and result['status']==case['expected_status']
                else:
                    ref=refs[(case['suite'],case['id'])];expected=f"{ref['entry_id']}:field:{ref['field_index']:03}"
                    rank=next((i for i,h in enumerate(result['hits'],1) if h['chunk_id']==expected and anchor_matches(h,ref)),None)
                    row.update(rank=rank,target_hit_at_5=rank is not None,
                               wrong_entity_at_1=bool(result['hits']) and result['hits'][0]['name']!=ref['name'],
                               other_entities_in_hits=any(h['name']!=ref['name'] for h in result['hits']))
                records.append(row);raw.append({'id':case['id'],'suite':case['suite'],**result})
            print(json.dumps({'method':method,'cases':len(questions),'completed':True}),flush=True)
        groups={}
        for method in ('bm25','name_field','name_bm25','dense','name_dense'):
            groups[method]={}
            for suite,group in sorted({(c['suite'],c['group']) for c in questions}):
                rows=[r for r in records if r['method']==method and r['suite']==suite and r['group']==group]
                data={'total':len(rows),'status_counts':dict(Counter(r['status'] for r in rows)),
                      'mean_query_ms':round(sum(r['query_ms'] for r in rows)/len(rows),3)}
                if group=='abstention':data['correct_abstentions']=sum(r['correct_abstention'] for r in rows)
                else:data.update(target_hit_at_5=sum(r['target_hit_at_5'] for r in rows),
                    mrr_at_5=sum(1/r['rank'] if r['rank'] else 0 for r in rows)/len(rows),
                    wrong_entity_at_1=sum(r['wrong_entity_at_1'] for r in rows),
                    queries_with_other_entities=sum(r['other_entities_in_hits'] for r in rows))
                groups[method][suite+'/'+group]=data
        report={'kind':'local_dense_development_comparison','methods':groups,'cases':records,
                'external_calls':0,'external_embedding_calls':0,'network_attempts':len(attempts),
                'human_gold':False,'business_accuracy':None,'model_load_ms':load_ms,'model':encoder.receipt,
                'recipe':RECIPE,'cache':dense.receipt,'index_sha256':lexical.receipt['index_sha256'],
                'suite_fingerprints':suite_hashes,'cpu_threads':args.threads,
                'local_query_embeddings':sum(r['query_encoding_ms']>0 for r in records),
                'code_sha256':{p:digest(ROOT/p) for p in ['tcm/local_dense.py','tcm/mineru_retrieval.py','scripts/run_local_dense_experiment.py']},
                'limits':['Development/corpus-assisted references, not independent gold.',
                          'One measured run per question/method after one synthetic encoder warmup; no stable P95 claimed.',
                          'Dense timing includes query encoding and exhaustive ranking; build and model load recorded separately.',
                          'Global dense has no calibrated abstention threshold; name_dense shares known-name/field guards.',
                          'No output generation or clinical correctness evaluation.']}
        (args.out/'evaluation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        (args.out/'query_results.json').write_text(json.dumps(raw,ensure_ascii=False,indent=2))
        print(json.dumps({'groups':groups,'network_attempts':len(attempts),'cache_seconds':dense.receipt['build_seconds']},ensure_ascii=False))
        if attempts:raise RuntimeError('Unexpected blocked network attempt; do not report offline success')


if __name__=='__main__':main()
