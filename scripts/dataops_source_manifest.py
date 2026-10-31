"""Fingerprint the research image's allowlisted source tree, including dirty/untracked code."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def manifest(root=ROOT):
    root=Path(root)
    files={root/name for name in ('Dockerfile.research','.dockerignore','requirements-tcm.txt',
        'requirements-research-preview.txt','requirements-research-chat.txt','constraints-research-chat.txt',
        'app.py','config.py','features.py','source_access.py','resources/source_manifest.json')}
    files.update((root/'agents').glob('*.py'));files.update((root/'agents/guardrails').glob('*.py'))
    for folder in ('tcm','templates','assets/web'):
        files.update(p for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts
                     and p.suffix not in ('.pyc','.sqlite3','.sqlite3-wal','.sqlite3-shm'))
    if any(p.is_symlink() or not p.is_file() for p in files):raise ValueError('Build sources missing or symlinked')
    hashes={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
    encoded=json.dumps(hashes,sort_keys=True,separators=(',',':')).encode()
    result={'kind':'research-image-allowlisted-source-v1','source_tree_sha256':hashlib.sha256(encoded).hexdigest(),
            'files':hashes,'data_env_and_runtime_excluded':True}
    try:
        result['git_commit']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True,stderr=subprocess.DEVNULL).strip()
        status=subprocess.check_output(['git','status','--porcelain','--untracked-files=normal'],cwd=root,text=True)
        result['git_worktree_dirty']=bool(status.strip())
    except (OSError,subprocess.CalledProcessError):
        result['git_commit']=None;result['git_worktree_dirty']=None
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--hash-only',action='store_true');args=p.parse_args()
    report=manifest();print(report['source_tree_sha256'] if args.hash_only else json.dumps(report,ensure_ascii=False,indent=2))
