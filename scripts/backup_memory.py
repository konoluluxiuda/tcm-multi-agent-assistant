"""Back up/inspect completed-turn memory; restore only to a new isolated database."""
import argparse
import json
import os
from pathlib import Path

from agents.memory_backup import BackupError, create_backup, inspect_backup, restore_backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    backup = commands.add_parser('backup')
    backup.add_argument('--source', type=Path, required=True)
    backup.add_argument('--out', type=Path, required=True)
    backup.add_argument('--ttl-days', type=int, default=int(os.getenv('CHAT_MEMORY_TTL_DAYS', '30')))
    backup.add_argument('--retention-days', type=int, default=7)
    inspect = commands.add_parser('inspect'); inspect.add_argument('--bundle', type=Path, required=True)
    restore = commands.add_parser('restore')
    restore.add_argument('--bundle', type=Path, required=True)
    restore.add_argument('--target', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'backup':
            result = create_backup(args.source, args.out, ttl_seconds=args.ttl_days*86400,
                                   retention_days=args.retention_days)
        elif args.command == 'inspect':
            result = inspect_backup(args.bundle)
        else:
            result = restore_backup(args.bundle, args.target)
        print(json.dumps(dict(result, status=result.get('status', 'ok')), indent=2))
        return 0
    except BackupError as exc:
        print(json.dumps({'status': 'error', 'error_code': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
