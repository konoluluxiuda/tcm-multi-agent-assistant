"""Return unreviewed research candidates and citations, without generating advice."""
import argparse
import json
from pathlib import Path
from tcm.mineru_retrieval import ResearchRetriever

ROOT = Path(__file__).resolve().parents[1]

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('question')
    parser.add_argument('--index', type=Path, default=ROOT / 'var/mineru_retrieval_v2')
    parser.add_argument('--method', choices=('name_field', 'bm25'), default='name_field')
    parser.add_argument('--name')
    parser.add_argument('--field')
    parser.add_argument('--k', type=int, default=5)
    args = parser.parse_args()
    retriever = ResearchRetriever(args.index)
    try:
        print(json.dumps(retriever.query(args.question, args.method, args.k, args.name, args.field),
                         ensure_ascii=False, indent=2))
    finally:
        retriever.close()
