"""Bounded stdin/stdout local BGE + ephemeral Qdrant memory worker. No downloads."""
import argparse
import json
import sys


def rank(payload, model):
    from tcm.local_dense import LocalEncoder
    from qdrant_client import QdrantClient, models
    docs=payload['documents'];query=payload['query']
    if not isinstance(query,str) or not 1<=len(query)<=400 or not 1<=len(docs)<=20:
        raise ValueError('Invalid memory batch')
    if any(not isinstance(d['text'],str) or not 1<=len(d['text'])<=700 for d in docs):
        raise ValueError('Invalid memory document')
    encoder=LocalEncoder(model,threads=2)
    vectors=encoder.encode([d['text'] for d in docs]);truncated=any(encoder.last_truncated)
    vector=encoder.encode([query],query=True)[0];truncated |= any(encoder.last_truncated)
    client=QdrantClient(location=':memory:')
    try:
        client.create_collection('projects',vectors_config=models.VectorParams(size=encoder.dimension,distance=models.Distance.COSINE))
        client.upsert('projects',points=[models.PointStruct(id=i,vector=v.tolist(),payload={'id':d['id']}) for i,(d,v) in enumerate(zip(docs,vectors))])
        hits=client.query_points('projects',query=vector.tolist(),limit=len(docs),with_payload=True).points
        return {'scores':{h.payload['id']:h.score for h in hits},'truncated':truncated}
    finally:
        client.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--model',required=True);args=parser.parse_args()
    raw=sys.stdin.read(24001)
    if len(raw)>24000:raise ValueError('Memory worker input too large')
    print(json.dumps(rank(json.loads(raw),args.model)))
