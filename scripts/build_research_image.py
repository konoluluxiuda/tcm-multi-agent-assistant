"""Build only allowlisted runtime files in a temporary context, never the data tree."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def files():
    names = ['Dockerfile.research', 'requirements-tcm.txt', 'constraints-research-chat.txt', 'requirements-research-preview.txt',
             'requirements-research-chat.txt', 'app.py', 'config.py', 'features.py', 'source_access.py',
             'resources/source_manifest.json']
    names += [str(p.relative_to(ROOT)) for p in (ROOT / 'agents').glob('*.py')]
    names += [str(p.relative_to(ROOT)) for p in (ROOT / 'agents/guardrails').glob('*.py')]
    names += [str(p.relative_to(ROOT)) for p in (ROOT / 'tcm').glob('*') if p.suffix in ('.py', '.html', '.js')]
    names += [str(p.relative_to(ROOT)) for p in (ROOT / 'templates').glob('*.html')]
    return sorted(set(names))


def build(tag, receipt):
    paths = files()
    fingerprint = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths}
    tree_sha = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip())
    with tempfile.TemporaryDirectory(prefix='tcm-research-build-') as temp:
        context = Path(temp)
        for name in paths:
            source, target = ROOT / name, context / name
            if source.is_symlink():
                raise ValueError('Build inputs must not be symlinks: ' + name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if hashlib.sha256(target.read_bytes()).hexdigest() != fingerprint[name]:
                raise ValueError('Build source changed while creating context: ' + name)
        subprocess.run(['docker', 'build', '-f', str(context / 'Dockerfile.research'),
                        '--build-arg', 'SOURCE_REVISION=' + revision + ('-working-tree' if dirty else ''),
                        '--build-arg', 'SOURCE_TREE_SHA256=' + tree_sha, '-t', tag, str(context)], check=True)
    image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', tag], text=True))[0]
    result = {'image_tag': tag, 'image_id': image['Id'], 'image_bytes': image['Size'],
              'base_revision': revision, 'working_tree_dirty': dirty, 'source_tree_sha256': tree_sha,
              'source_files_sha256': fingerprint, 'build_context_bytes': sum((ROOT / n).stat().st_size for n in paths),
              'context_policy': 'temporary allowlisted runtime files; no corpus, .env, weights or sessions',
              'docker_hub_published': False}
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', default='tcm-research-chat:local')
    parser.add_argument('--receipt', type=Path, default=ROOT / 'var/docker_research_v1/build.json')
    args = parser.parse_args()
    result = build(args.tag, args.receipt)
    print(json.dumps({k: result[k] for k in ('image_tag', 'image_id', 'image_bytes', 'source_tree_sha256')}))
