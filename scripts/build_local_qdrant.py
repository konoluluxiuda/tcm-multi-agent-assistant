"""Build an immutable Qdrant generation from already verified local BGE vectors."""
import argparse
from pathlib import Path
from tcm.mineru_retrieval import ResearchRetriever
from tcm.local_dense import LocalEncoder,DenseRetriever
from tcm.qdrant_dense import build_generation


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index',type=Path,default=Path('var/mineru_retrieval_v2'))
    parser.add_argument('--model',type=Path,default=Path('var/evidence_assistant_v1/local_bge_small_zh'))
    parser.add_argument('--cache',type=Path,default=Path('var/evidence_assistant_v1/local_dense_cache'))
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    import socket
    def deny(*args,**kwargs):raise RuntimeError('Offline builder forbids networking')
    for name in ('connect','connect_ex','sendto'):setattr(socket.socket,name,deny)
    socket.getaddrinfo=deny
    r=ResearchRetriever(args.index)
    try:
        dense=DenseRetriever(r,LocalEncoder(args.model),args.cache)
        receipt=build_generation(dense,args.out)
        print({'status':'built','count':receipt['count'],'dimension':receipt['dimension']})
    finally:r.close()


if __name__=='__main__':main()
