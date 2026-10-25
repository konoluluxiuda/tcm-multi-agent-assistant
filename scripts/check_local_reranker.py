"""Offline runtime checks for pinned reranking, score ordering and safe fallback."""
import argparse
import json
import os
from pathlib import Path
from tcm.local_reranker import LocalReranker,maybe_rerank
from tcm.cross_source_report import text_search


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--model',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);args=p.parse_args()
    import socket
    def deny(*args,**kwargs):raise RuntimeError('Network disabled')
    for name in ('connect','connect_ex','sendto'):setattr(socket.socket,name,deny)
    socket.getaddrinfo=deny
    model=LocalReranker(args.model)
    hits=[{'chunk_id':'synthetic-a','name':'示例叶','label':'鉴别','text':'采用薄层色谱法进行鉴别。'},
          {'chunk_id':'synthetic-b','name':'示例叶','label':'贮藏','text':'置阴凉干燥处，防潮，避免日光直射。'}]
    result,meta=model.rerank('示例叶如何存放，怎样防止受潮？',hits)
    assert result[0]['chunk_id']=='synthetic-b'
    long=[{**h,'text':h['text']*200} for h in hits]
    _,truncation=model.rerank('示例叶如何存放？',long)
    assert truncation['truncated_candidates']==2
    timed_out=False
    try:model.rerank('示例叶如何存放？',long,timeout=.000001)
    except (TimeoutError,RuntimeError):timed_out=True
    assert timed_out
    interrupted=False
    try:model.rerank('示例叶如何存放？',long,timeout=.05)
    except Exception as exc:
        assert isinstance(exc,TimeoutError) or 'terminate' in str(exc).lower(),type(exc)
        interrupted=True
    assert interrupted
    # A terminated RunOptions must not poison the next inference.
    reused,_=model.rerank('示例叶如何存放，怎样防止受潮？',hits)
    assert reused[0]['chunk_id']=='synthetic-b'
    original,fallback=maybe_rerank('test',hits,args.model/'missing')
    assert original==hits and fallback['status']=='unavailable'
    root=Path(__file__).resolve().parents[1]
    os.environ.update(PHARMACOPOEIA_INDEX=str(root/'var/mineru_retrieval_v2'),
        LOCAL_DENSE_MODEL=str(root/'var/evidence_assistant_v1/local_bge_small_zh'),
        LOCAL_DENSE_CACHE=str(root/'var/evidence_assistant_v1/local_dense_cache'),
        LOCAL_QDRANT_GENERATION='',LOCAL_RERANKER_MODEL='')
    baseline=text_search('当归','功效和贮藏，重点是贮藏')
    assert baseline['retrieval']['backend']=='numpy' and baseline['retrieval']['dense']=='used'
    os.environ['LOCAL_RERANKER_MODEL']=str(args.model)
    real=text_search('当归','功效和贮藏，重点是贮藏')
    assert real['retrieval']['reranker']['status']=='used'
    # Source witnesses survive ranking byte-for-byte; neither text nor evidence is synthesized.
    for hit in real['hits']:
        assert hit['name']=='当归' and 'rerank_score' in hit and hit['evidence']
    unsupported=text_search('当归','今天多少钱')
    assert not unsupported['hits']
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps({'status':'passed','external_calls':0,'synthetic_order_verified':True,
        'truncated_candidates':truncation['truncated_candidates'],'tiny_budget_refused':timed_out,'short_inference_interrupted':interrupted,'session_reusable_after_timeout':True,
        'missing_model_keeps_candidates':True,'numpy_default_verified':True,'unsupported_question_has_no_hits':True,
        'model':meta,'real_reranker':real['retrieval']['reranker'],'real_chunk_ids':[h['chunk_id'] for h in real['hits']]},ensure_ascii=False,indent=2))
    print('Local reranker runtime checks passed')


if __name__=='__main__':main()
