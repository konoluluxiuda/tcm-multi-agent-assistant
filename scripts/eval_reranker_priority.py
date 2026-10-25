"""Explicit-priority development diagnostics, separately reported from retrieval regression."""
import argparse
import json
import os
from pathlib import Path
import time
from tcm.cross_source_report import text_search
from tcm.mineru_pharmacopoeia import digest


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--model',type=Path,required=True)
 p.add_argument('--out',type=Path,required=True);args=p.parse_args()
 root=Path(__file__).resolve().parents[1];questions=root/'eval/reranker_priority_dev_v1.json'
 suite=json.loads(questions.read_text())
 os.environ.update(PHARMACOPOEIA_INDEX=str(root/'var/mineru_retrieval_v2'),
  LOCAL_DENSE_MODEL=str(root/'var/evidence_assistant_v1/local_bge_small_zh'),
  LOCAL_DENSE_CACHE=str(root/'var/evidence_assistant_v1/local_dense_cache'),
  LOCAL_QDRANT_GENERATION=str(root/'var/qdrant_research_v1/generation'))
 manifest=json.loads((root/'var/mineru_retrieval_v2/manifest.json').read_text())
 assert suite['source_view_sha256']==manifest['view_sha256']
 import socket
 def deny(*args,**kwargs):raise RuntimeError('No network')
 for key in ('connect','connect_ex','sendto'):setattr(socket.socket,key,deny)
 socket.getaddrinfo=deny
 rows=[]
 for case in suite['cases']:
  for method in ('rrf','rrf_priority_rule','rrf_rerank'):
   os.environ['LOCAL_RERANKER_MODEL']=str(args.model.resolve()) if method=='rrf_rerank' else ''
   started=time.monotonic();result=text_search(case['herb'],case['question'])
   if method=='rrf_priority_rule':
    # Query-only literal priority baseline; never consult expected_primary_field.
    focus=case['question'].rsplit('重点是',1)[-1]
    label={'功效':'功能与主治','贮藏':'贮藏'}.get(focus)
    result['hits']=sorted(result['hits'],key=lambda h: h['label']!=label)
   ids=[h['chunk_id'] for h in result['hits']]
   row={'id':case['id'],'method':method,'status':result['status'],'hit_ids':ids,
    'priority_at_1':bool(ids) and ids[0]==case['expected_chunk_id'],
    'target_present':case['expected_chunk_id'] in ids,'ms':round((time.monotonic()-started)*1000,2),
    'retrieval':result.get('retrieval')}
   rows.append(row);print(json.dumps({'id':case['id'],'method':method,'priority_at_1':row['priority_at_1']}),flush=True)
 summary={}
 for method in ('rrf','rrf_priority_rule','rrf_rerank'):
  values=[r for r in rows if r['method']==method]
  summary[method]={'total':len(values),'priority_at_1':sum(r['priority_at_1'] for r in values),
   'target_present':sum(r['target_present'] for r in values),'mean_ms':sum(r['ms'] for r in values)/len(values),
   'reranker_used':sum(isinstance(r['retrieval'],dict) and isinstance(r['retrieval'].get('reranker'),dict) and r['retrieval']['reranker'].get('status')=='used' for r in values)}
 args.out.parent.mkdir(parents=True,exist_ok=True)
 args.out.write_text(json.dumps({'evaluation':'explicit_priority_development_not_independent',
  'question_sha256':digest(questions),'summary':summary,'cases':rows},ensure_ascii=False,indent=2))
 print(json.dumps(summary,indent=2))
 if not summary['rrf_rerank']['reranker_used']:raise SystemExit('No successful real reranker calls')


if __name__=='__main__':main()
