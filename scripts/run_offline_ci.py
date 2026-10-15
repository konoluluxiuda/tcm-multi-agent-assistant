"""Run fixture-only regressions in isolated processes; skips never mean pass."""
import argparse
import ast
import faulthandler
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def test_environment():
    # Keep OS facilities, but do not inherit provider credentials or tracing.
    allowed = ('PATH', 'HOME', 'SYSTEMROOT', 'TEMP', 'TMP', 'LANG')
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update({
        'PYTHONPATH': str(ROOT), 'PYTHONUTF8': '1',
        'CHAT_MODEL': 'offline-test', 'CHAT_API_KEY': 'test-only',
        'CHAT_BASE_URL': 'http://127.0.0.1:9/v1',
        'EMBEDDING_MODEL': 'offline-test', 'EMBEDDING_API_KEY': 'test-only',
        'EMBEDDING_BASE_URL': 'http://127.0.0.1:9/v1', 'EMBEDDING_DIM': '1024',
        'ENABLE_IMAGE_ANALYSIS': '0', 'ENABLE_SPEECH': '0', 'ENABLE_WEB_SEARCH': '0',
        'TAVILY_API_KEY': '', 'ELEVEN_LABS_API_KEY': '',
        'LANGCHAIN_TRACING_V2': 'false', 'LANGSMITH_TRACING': 'false',
    })
    return env


def worker(path, output):
    import dotenv
    attempts = []

    def deny_network(*args, **kwargs):
        attempts.append('blocked')
        raise RuntimeError('Network access forbidden in offline regression')

    # Patch before loading tests. ASGI TestClient uses in-process transport.
    with tempfile.TemporaryDirectory(prefix='tcm-test-memory-') as memory_dir, \
         patch.dict(os.environ, {'CHAT_MEMORY_DB': str(Path(memory_dir) / 'memory.sqlite3')}), \
         patch.object(dotenv, 'load_dotenv', return_value=False), \
         patch.object(socket.socket, 'connect', deny_network), \
         patch.object(socket.socket, 'connect_ex', deny_network), \
         patch.object(socket.socket, 'sendto', deny_network), \
         patch.object(socket, 'getaddrinfo', deny_network):
        suite = unittest.defaultTestLoader.discover(str(path.parent), pattern=path.name)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    passed = (result.wasSuccessful() and not result.skipped and
              not result.expectedFailures and not attempts and result.testsRun > 0)
    report = {
        'module': path.name, 'status': 'passed' if passed else 'failed',
        'total': result.testsRun, 'failures': len(result.failures),
        'errors': len(result.errors), 'skipped': len(result.skipped),
        'expected_failures': len(result.expectedFailures),
        'network_attempts': len(attempts),
    }
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return 0 if passed else 1


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    # Syntax validation of tracked code, without importing heavyweight modules.
    tracked = subprocess.check_output(['git', 'ls-files', '*.py'], cwd=ROOT, text=True).splitlines()
    candidates = set(tracked) | {str(p.relative_to(ROOT)) for p in (ROOT / 'scripts').glob('*.py')}
    for name in sorted(candidates):
        ast.parse((ROOT / name).read_text(encoding='utf-8'), filename=name)
    modules = []
    for path in sorted((ROOT / 'tests').glob('test_*.py')):
        target = output / (path.stem + '.json')
        target.unlink(missing_ok=True)
        try:
            # Working directory stays at the repo for the golden evaluator's CLI
            # subprocess; test fixtures themselves use TemporaryDirectory.
            with (output / (path.stem + '.log')).open('w', encoding='utf-8') as log:
                proc = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve()), '--worker', str(path),
                     '--worker-output', str(target)], cwd=ROOT, env=test_environment(),
                    stdout=log, stderr=subprocess.STDOUT, text=True, timeout=120,
                )
            record = json.loads(target.read_text()) if target.exists() else {
                'module': path.name, 'status': 'failed', 'reason': 'worker produced no report',
            }
            if proc.returncode != 0:
                record['status'] = 'failed'
        except subprocess.TimeoutExpired:
            record = {'module': path.name, 'status': 'failed', 'reason': '120s timeout'}
        modules.append(record)
        print(f"{path.name}: {record['status']} ({record.get('total', 0)} tests)", flush=True)
    success = bool(modules) and all(row['status'] == 'passed' for row in modules)
    report = {
        'status': 'passed' if success else 'failed',
        'python': sys.version.split()[0], 'syntax_files': len(candidates),
        'total': sum(row.get('total', 0) for row in modules),
        'skipped': sum(row.get('skipped', 0) for row in modules), 'modules': modules,
        'full_snapshot_evaluation': {'status': 'not_run', 'reason': 'requires private ETCM snapshot/database; separate local acceptance'},
        'live_provider_evaluation': {'status': 'not_run', 'reason': 'offline regression uses deterministic substitutes'},
    }
    (output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return 0 if success else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'var/ci')
    parser.add_argument('--worker', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--worker-output', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if not args.worker_output:
            parser.error('--worker-output is required')
        faulthandler.dump_traceback_later(60, repeat=True)
        try:
            return worker(args.worker.resolve(), args.worker_output.resolve())
        finally:
            faulthandler.cancel_dump_traceback_later()
    return run(args.out.resolve())


if __name__ == '__main__':
    sys.exit(main())
