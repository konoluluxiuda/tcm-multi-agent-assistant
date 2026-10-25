"""Compare entity-filtered baselines and local RRF on existing development labels."""
import argparse
import json
import os
from pathlib import Path
import time
from tcm.cross_source_report import text_search
from tcm.mineru_retrieval import ResearchRetriever, resolve_names
from tcm.mineru_pharmacopoeia import digest
from scripts.eval_mineru_retrieval import anchor_matches


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]
    os.environ.update(PHARMACOPOEIA_INDEX=str(root/'var/mineru_retrieval_v2'),
        LOCAL_DENSE_MODEL=str(root/'var/evidence_assistant_v1/local_bge_small_zh'),
        LOCAL_DENSE_CACHE=str(root/'var/evidence_assistant_v1/local_dense_cache'))
    import socket
    def deny(*args,**kwargs):raise RuntimeError('Network disabled')
    for name in ('connect','connect_ex','sendto'):setattr(socket.socket,name,deny)
    socket.getaddrinfo=deny
    suites=[('regression','mineru_retrieval_questions_v1','mineru_retrieval_reference_v2'),
            ('body_dev','scoped_retrieval_dev_v1','scoped_retrieval_reference_v1'),
            ('semantic_dev','dense_semantic_dev_v1','dense_semantic_reference_v1')]
    rows=[];r=ResearchRetriever(os.environ['PHARMACOPOEIA_INDEX']);inputs={}
    try:
        for suite,qfile,rfile in suites:
            qp=root/'eval'/f'{qfile}.json';rp=root/'eval'/f'{rfile}.json'
            questions=json.loads(qp.read_text());refs=json.loads(rp.read_text())
            assert refs['question_sha256']==digest(qp)
            assert not refs.get('source_view_sha256') or refs['source_view_sha256']==r.receipt['view_sha256']
            inputs[suite]={'questions':digest(qp),'references':digest(rp)}
            reference={x['case_id']:x for x in refs['references']}
            for case in questions['cases']:
                names=resolve_names(case['question'],r.names)
                for method in ('name_field','name_bm25','hybrid'):
                    start=time.monotonic()
                    if method=='hybrid' and len(names)==1:result=text_search(names[0],case['question'])
                    else:result=r.query(case['question'],method='name_field' if method=='hybrid' else method,k=5)
                    hits=result['hits'][:5]
                    row={'suite':suite,'id':case['id'],'method':method,'status':result['status'],
                         'ms':round((time.monotonic()-start)*1000,2),'dense':result.get('retrieval'),
                         'hit_ids':[h['chunk_id'] for h in hits]}
                    if case['group']=='abstention':row['correct_abstention']=not hits and result['status']==case['expected_status']
                    else:
                        ref=reference[case['id']];expected=f"{ref['entry_id']}:field:{ref['field_index']:03}"
                        rank=next((i for i,h in enumerate(hits,1) if h['chunk_id']==expected and anchor_matches(h,ref)),None)
                        row.update(target_hit_at_5=rank is not None,reciprocal_rank=1/rank if rank else 0,
                                   wrong_entity=any(h['name']!=ref['name'] for h in hits))
                    rows.append(row)
        summaries={}
        for method in ('name_field','name_bm25','hybrid'):
            selected=[r for r in rows if r['method']==method];positive=[r for r in selected if 'target_hit_at_5' in r]
            summaries[method]={'positive_cases':len(positive),'target_hits_at_5':sum(r['target_hit_at_5'] for r in positive),
              'mrr_at_5':sum(r['reciprocal_rank'] for r in positive)/len(positive),
              'wrong_entity_cases':sum(r['wrong_entity'] for r in positive),
              'abstention_cases':len(selected)-len(positive),'correct_abstentions':sum(r.get('correct_abstention',False) for r in selected),
              'mean_ms':sum(r['ms'] for r in selected)/len(selected)}
        report={'evaluation_type':'existing_development_not_independent','recall_claimed':False,
                'view_sha256':r.receipt['view_sha256'],'inputs':inputs,'summary':summaries,'cases':rows}
        args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(summaries,ensure_ascii=False,indent=2))
    finally:r.close()


if __name__=='__main__':main()
