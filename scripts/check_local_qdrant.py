"""Offline real Qdrant vs numpy parity, repeated reads and corrupt-generation checks."""
import argparse
import asyncio
import sys
import json
import os
from pathlib import Path
import tempfile
import time
from tcm.local_dense import DenseRetriever,LocalEncoder
from tcm.qdrant_dense import QdrantDenseRetriever,build_generation
from tcm.mineru_retrieval import ResearchRetriever


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--generation',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);args=p.parse_args()
    import socket
    def deny(*args,**kwargs):raise RuntimeError('Network forbidden')
    for name in ('connect','connect_ex','sendto'):setattr(socket.socket,name,deny)
    socket.getaddrinfo=deny
    r=ResearchRetriever('var/mineru_retrieval_v2');encoder=LocalEncoder(Path('var/evidence_assistant_v1/local_bge_small_zh'))
    cache=Path('var/evidence_assistant_v1/local_dense_cache')
    baseline=DenseRetriever(r,encoder,cache);q=QdrantDenseRetriever(r,encoder,cache,args.generation)
    cases=[]
    try:
        for question in ['当归的功效','黄芪的归经','陈皮的贮藏','白术的食少记载','当归的功效']:
            expected=baseline.query(question,method='name_dense',k=5)
            start=time.monotonic();actual=q.query(question,method='name_dense',k=5)
            ids=lambda result:[x['chunk_id'] for x in result['hits']]
            assert ids(expected)==ids(actual),(question,ids(expected),ids(actual))
            assert expected['status']==actual['status']
            assert all(abs(a['score']-b['score'])<1e-5 for a,b in zip(expected['hits'],actual['hits']))
            cases.append({'question':question,'status':actual['status'],'ids':ids(actual),'parity':True,'qdrant_ms':round((time.monotonic()-start)*1000,2)})
        try:build_generation(baseline,args.generation)
        except FileExistsError:pass
        else:raise AssertionError('Existing generation overwritten')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);receipt=json.loads((args.generation/'receipt.json').read_text())
            receipt['index_sha256']='changed';(root/'receipt.json').write_text(json.dumps(receipt))
            try:QdrantDenseRetriever(r,encoder,cache,root)
            except ValueError:pass
            else:raise AssertionError('Version mismatch accepted')
        # Corrupt only the verifier's in-memory expected hash, not the real source files.
        name=next(iter(q.qreceipt['files']));q.qreceipt['files'][name]='changed'
        try:q.query('当归的功效',method='name_dense',k=5)
        except ValueError:pass
        else:raise AssertionError('Corruption accepted')
        from tcm.cross_source_report import local_branch
        os.environ.update(PHARMACOPOEIA_INDEX=str(Path('var/mineru_retrieval_v2').resolve()),
            LOCAL_DENSE_MODEL=str(Path('var/evidence_assistant_v1/local_bge_small_zh').resolve()),
            LOCAL_DENSE_CACHE=str(cache.resolve()), LOCAL_QDRANT_GENERATION=str(args.generation.resolve()),
            LOCAL_DENSE_PYTHON=sys.executable)
        async def concurrent():
            return await asyncio.gather(*(local_branch('text','当归','功能与主治') for _ in range(4)))
        concurrent_results=asyncio.run(concurrent())
        assert all(x['retrieval'].get('backend')=='qdrant_local' and x['retrieval']['dense']=='used' for x in concurrent_results)
        assert len({tuple(h['chunk_id'] for h in x['hits']) for x in concurrent_results})==1
        os.environ['LOCAL_QDRANT_GENERATION']=str(args.generation/'missing-generation')
        fallback=asyncio.run(local_branch('text','当归','功能与主治'))
        assert fallback['hits'] and fallback['retrieval']['dense']=='unavailable'
        assert fallback['retrieval']['backend']=='qdrant_local'
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps({'status':'passed','network_calls':0,'backend':'qdrant_local',
            'count':q.receipt['count'],'dimension':q.receipt['dimension'],'cases':cases,
            'unavailable_generation_keeps_lexical':True,'concurrent_processes':4,'concurrent_all_used_qdrant':True,'existing_generation_refused':True,'version_mismatch_refused':True,'checksum_mismatch_refused':True},ensure_ascii=False,indent=2))
        print('Qdrant parity and failure checks passed')
    finally:r.close()


if __name__=='__main__':main()
