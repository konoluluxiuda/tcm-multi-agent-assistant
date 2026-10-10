"""Run with python -m tcm; JSON output suitable for local tools."""
import argparse
import json
import sqlite3
import sys
from .herbs import import_snapshot, lookup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default='var/tcm/herbs.sqlite3')
    sub = parser.add_subparsers(dest='command', required=True)
    ingest = sub.add_parser('import', help='Import a complete herb-directory snapshot')
    ingest.add_argument('--source', required=True)
    ingest.add_argument('--version', required=True, help='Local dataset snapshot label, not official ETCM version')
    query = sub.add_parser('query', help='Exact source-record lookup')
    group = query.add_mutually_exclusive_group(required=True)
    group.add_argument('--name')
    group.add_argument('--id')
    query.add_argument('--version')
    args = parser.parse_args()
    try:
        if args.command == 'import':
            result = import_snapshot(args.source, args.db, args.version)
        else:
            result = lookup(args.db, name=args.name, source_record_id=args.id, dataset_version=args.version)
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
