"""Local synthetic-driver delivery check with an independent storage reference."""
import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit

import httpx
from openpyxl import load_workbook


def verify_delivery(http, doc, reference):
    assert doc['status'] == 'complete'
    result = doc['result']
    row = next(r for r in result['rows'] if any(
        result['evidence'][key]['source'].get('entry_id') == reference['entry_id']
        for key in r['cells']['贮藏']['evidence_ids']))
    value = row['cells']['贮藏']['value']
    assert hashlib.sha256(value.encode()).hexdigest() == reference['expected_storage_sha256']
    refs = [result['evidence'][k]['source'] for k in row['cells']['贮藏']['evidence_ids']]
    assert len(refs) == 1 and refs[0]['scope'] == reference['scope']
    assert [x['block_id'] for x in refs[0]['payload']['evidence']] == [s['block_id'] for s in reference['storage_segments']]
    response = http.get('/api/dataops/workspaces/'+doc['id']+'/artifact'); response.raise_for_status()
    assert hashlib.sha256(response.content).hexdigest() == doc['artifact']['sha256']
    book = load_workbook(BytesIO(response.content), read_only=True, data_only=False)
    try:
        rows = list(book['结果'].values); column = rows[0].index('贮藏')
        match = next(r for r in rows[1:] if r[0] == row['source_id'])
        assert match[column] == value
    finally: book.close()
    return {'workspace_id': doc['id'], 'revision': doc['revision'],
            'storage_matches_original_block_reference': True, 'storage_citation_exact': True,
            'download_hash_verified': True, 'xlsx_storage_value_verified': True}


def wait(http, identifier):
    deadline = time.monotonic()+60
    while time.monotonic() < deadline:
        response = http.get('/api/dataops/agent-runs/'+identifier); response.raise_for_status()
        data = response.json()
        if data['job']['status'] not in ('queued','running'):
            assert data['workspace']['status'] == 'complete', data['workspace']['status']
            return data['workspace']
        time.sleep(.2)
    raise TimeoutError('Local synthetic task did not complete')


def check(url, references):
    if urlsplit(url).hostname != '127.0.0.1' or urlsplit(url).scheme != 'http':
        raise ValueError('Only localhost allowed')
    reference = next(c for c in json.loads(references.read_text())['cases'] if c['name']=='黄芪')
    with httpx.Client(base_url=url, timeout=30, trust_env=False) as http:
        runtime = http.get('/api/runtime').json()
        assert runtime['restart_required'] is False and runtime['dataops']['driver']=='synthetic'
        assert runtime['dataops']['external_model_enabled'] is False and runtime['intent_model_enabled'] is False
        http.get('/conversation').raise_for_status()
        response = http.post('/api/dataops/workspaces', json={'request_key': 'storage-F-'+secrets.token_hex(8),
            'goal': {'query': {'names': ['白术','黄芪']}, 'document_fields': ['贮藏']}})
        response.raise_for_status(); fixed = verify_delivery(http, response.json(), reference)
        def submit(question, parent=None):
            body = {'question': question, 'request_key': 'storage-A-'+secrets.token_hex(8)}
            if parent: body.update(parent_id=parent['id'], expected_revision=parent['revision'])
            response = http.post('/api/dataops/task-requests', json=body); response.raise_for_status()
            return wait(http, response.json()['workspace']['id'])
        first = submit('整理白术和黄芪的功效、注意事项，保留出处并生成表格')
        before = first['artifact']['sha256']
        second = submit('增加贮藏，保留旧字段', first)
        dynamic = verify_delivery(http, second, reference)
        assert second['progress']['reused_cells'] == 8 and second['revision']==2
        old = http.get('/api/dataops/workspaces/'+first['id']+'/artifact'); old.raise_for_status()
        assert hashlib.sha256(old.content).hexdigest()==before
    return {'status': 'passed', 'fixed': fixed, 'dynamic_incremental': dynamic,
            'reused_cells': 8, 'v1_file_preserved': True, 'external_model_calls': 0,
            'driver': 'synthetic', 'real_data': True, 'quality_reference_human_signoff': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', default='http://127.0.0.1:7912')
    p.add_argument('--references', type=Path, default=Path('eval/pharmacopoeia_storage_boundary_v1.json'))
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    if args.out.exists(): p.error('Use a new output path to preserve previous evidence')
    result = check(args.url, args.references)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__': main()
