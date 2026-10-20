"""Check the recorded development/regression inputs; does not run evaluations."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'eval/application_baseline_v1.json'


def check(manifest, root=ROOT):
    errors = []
    if manifest.get('independent_acceptance_available') is not False:
        errors.append('This baseline cannot claim independent acceptance')
    files = manifest.get('files')
    if not isinstance(files, list) or not files:
        return errors + ['files must be a nonempty list']
    seen = set()
    for item in files:
        if not isinstance(item, dict):
            errors.append('invalid file entry')
            continue
        name = item.get('path', '')
        if not isinstance(name, str) or not name:
            errors.append('missing path')
            continue
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or name in seen:
            errors.append(f'{name}: duplicate or outside repository')
            continue
        seen.add(name)
        if item.get('role') not in ('development', 'regression', 'reference'):
            errors.append(f'{name}: invalid baseline role')
        try:
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != item.get('sha256'):
                errors.append(f'{name}: SHA-256 mismatch; preserve v1 and version changed inputs')
            document = json.loads(raw)
            if 'case_count' in item and len(document['cases']) != item['case_count']:
                errors.append(f'{name}: case count mismatch')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f'{name}: cannot check input ({type(exc).__name__})')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=DEFAULT)
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
        if not isinstance(manifest, dict):
            raise ValueError('manifest must be an object')
        errors = check(manifest)
    except (OSError, ValueError) as exc:
        errors = [f'Cannot read manifest ({type(exc).__name__})']
    print(json.dumps({'status': 'failed' if errors else 'passed',
                      'scope': 'input_integrity_only',
                      'independence_verified': False, 'errors': errors}, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
