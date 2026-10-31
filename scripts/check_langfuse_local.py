"""Read back local DataOps traces. Does not run tasks or call a model."""
import argparse
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values


def check(env_file, minimum=2, synthetic=False):
    config = dotenv_values(env_file)
    url = config['LANGFUSE_BASE_URL']
    if urlsplit(url).hostname not in ('localhost', '127.0.0.1'):
        raise ValueError('This check only reads local Langfuse')
    auth = (config['LANGFUSE_PUBLIC_KEY'], config['LANGFUSE_SECRET_KEY'])
    with httpx.Client(base_url=url, auth=auth, timeout=10, trust_env=False) as http:
        health = http.get('/api/public/health'); health.raise_for_status()
        http.get('/api/public/projects').raise_for_status()
        deadline = time.monotonic()+30
        while True:
            response = http.get('/api/public/v2/observations', params={
                'name': 'dataops.execution', 'limit': 100, 'fields': 'core,basic,metadata,io'})
            response.raise_for_status()
            roots = [x for x in response.json()['data'] if not x.get('parentObservationId')]
            if len(roots) >= minimum: break
            if time.monotonic() >= deadline: raise AssertionError('Insufficient ingested execution traces')
            time.sleep(1)
        verified = []
        for root in roots[:minimum]:
            response = http.get('/api/public/v2/observations', params={
                'traceId': root['traceId'], 'limit': 1000, 'fields': 'core,basic,metadata'})
            response.raise_for_status(); nodes = response.json()['data']
            ids = {n['id'] for n in nodes}
            assert all(not n.get('parentObservationId') or n['parentObservationId'] in ids for n in nodes)
            metadata = root['metadata']
            assert metadata.get('workspace_id') and root.get('input') and root.get('output')
            generations = sum(n['type'] == 'GENERATION' for n in nodes)
            if synthetic:
                assert metadata.get('driver') == 'synthetic_observation_policy'
                assert metadata.get('provider_requests') == 0 and generations == 0
            verified.append({'trace_id': root['traceId'], 'workspace_id': metadata['workspace_id'],
                'revision': metadata.get('revision'), 'observations': len(nodes), 'generations': generations,
                'parent_links_verified': True, 'has_bounded_input_output': True})
    return {'status': 'passed', 'server_version': health.json()['version'],
            'project_authentication': True, 'synthetic_only_check': synthetic, 'traces': verified}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env', type=Path, default=Path('var/langfuse_local/app.env'))
    p.add_argument('--minimum', type=int, default=2)
    p.add_argument('--expect-synthetic', action='store_true')
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    if not 1 <= args.minimum <= 100: p.error('--minimum must be 1..100')
    if args.out.exists(): p.error('Preserve earlier evidence; use a new output path')
    result = check(args.env, args.minimum, args.expect_synthetic)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__': main()
