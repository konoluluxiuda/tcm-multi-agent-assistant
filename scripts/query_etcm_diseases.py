"""Inspect one exact source disease name or ID without network/model calls."""
import argparse
from pathlib import Path
from tcm.disease_catalog import dumps, lookup, verify_witness


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--name', required=True, help='Exact source name or etcm-disease source ID')
    parser.add_argument('--page', type=int, default=1)
    parser.add_argument('--verify-source', type=Path, help='Verify all displayed rows against original files')
    args = parser.parse_args()
    result = lookup(args.database, args.name, page=args.page)
    if args.verify_source and result['resolution_status'] == 'source_record':
        for row in result['rows']:
            verify_witness(args.verify_source, result['profile'], row)
        result['verified_displayed_rows'] = len(result['rows'])
    print(dumps(result))


if __name__ == '__main__':
    main()
