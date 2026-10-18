"""Search PubMed, or replay a locally stored search without network access."""
import argparse
import json
from pathlib import Path

from tcm.literature_store import LiteratureStore
from tcm.pubmed import PubMedClient

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--query', help='Explicit PubMed query; no automatic herb-name translation')
    mode.add_argument('--run-id', help='Replay a saved snapshot; does not query PubMed')
    parser.add_argument('--limit', type=int, choices=range(1, 21), default=5)
    parser.add_argument('--db', type=Path, default=ROOT / 'var/literature/pubmed.sqlite3')
    args = parser.parse_args()
    if args.query is not None and (not args.query.strip() or len(args.query) > 2000):
        parser.error('--query must contain 1–2000 characters')
    store = LiteratureStore(args.db)
    try:
        if args.run_id:
            result = store.get(args.run_id)
            if result is None:
                print(json.dumps({'status': 'run_not_found'}))
                return 2
            run_id = args.run_id
        else:
            result = PubMedClient().search(args.query, args.limit)
            run_id = store.save(result)
            result = store.get(run_id)
        print(json.dumps(dict(result, run_id=run_id), ensure_ascii=False, indent=2))
        return 0 if result['status'] in ('ok', 'no_results') else 2
    finally:
        store.close()


if __name__ == '__main__':
    raise SystemExit(main())
