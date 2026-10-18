"""Build a fingerprinted research-only index, exception queue and sampling manifest."""
import argparse
import json
from pathlib import Path
import shutil

from tcm.pharmacopoeia import digest
from tcm.pharmacopoeia_comparison import build_comparison, validate_page
from tcm.pharmacopoeia_quality import evaluate
from tcm.pharmacopoeia_research import build_index

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def build(base, policy_path, output, prepared_dir, runs):
    if output.exists(): raise FileExistsError('Use a fresh batch directory')
    data = json.loads((base/'comparison.json').read_text())
    prepared_path = prepared_dir/'prepared.json'
    if digest(prepared_path) != data['prepared_sha256']: raise ValueError('Prepared manifest changed')
    prepared = json.loads(prepared_path.read_text())
    suite_path = ROOT/'eval/pharmacopoeia_ocr_sample_v1.json'
    if digest(suite_path) != data['manual_regions_sha256']: raise ValueError('Manual region manifest changed')
    suite = json.loads(suite_path.read_text())
    for source in suite['documents'].values():
        if digest(ROOT/source['path']) != source['sha256']: raise ValueError('Source PDF changed')
    registry = {p['key']: p for p in prepared['pages']}
    engines = {}
    for engine, directory in runs.items():
        expected = data['inputs'][engine]
        if digest(directory/'report.json') != expected['report_sha256']: raise ValueError('OCR report changed')
        pages = {}
        for key, sha256 in expected['page_results'].items():
            path = directory/f'{key}.json'
            if digest(path) != sha256: raise ValueError('OCR page result changed')
            page = json.loads(path.read_text()); validate_page(page, registry[key])
            page['result_sha256'] = sha256; pages[key] = page
        engines[engine] = pages
    reconstructed = build_comparison(suite, engines)
    actual = {(r['id'], f['key']): f['engines'] for r in data['records'] for f in r['fields']}
    rebuilt = {(r['id'], f['key']): f['engines'] for r in reconstructed['records'] for f in r['fields']}
    if actual != rebuilt: raise ValueError('Field candidates no longer match raw OCR extraction')
    policy = json.loads(policy_path.read_text())
    batch = evaluate(data, registry, policy)
    batch.update(source_comparison_sha256=digest(base/'comparison.json'), policy_sha256=digest(policy_path),
                 source_pdf_authenticity_verified=False,
                 ai_visual_drafts_used_for_quality_decisions=False,
                 code_sha256={name: digest(ROOT/name) for name in [
                     'tcm/pharmacopoeia_quality.py','tcm/pharmacopoeia_research.py','scripts/build_pharmacopoeia_quality.py']})
    output.mkdir(parents=True)
    shutil.copytree(base/'assets', output/'assets')
    (output/'index.html').write_text((ROOT/'tcm/pharmacopoeia_research.html').read_text(), encoding='utf-8')
    write(output/'quality.json', batch)
    batch_sha = digest(output/'quality.json')
    build_index(output/'research.sqlite3', batch, batch_sha)
    write(output/'review_queue.json', {'batch_sha256': batch_sha, 'human_signoff': False,
        'fields': [f for f in batch['fields'] if f['id'] in batch['review_queue']]})
    write(output/'sampling_manifest.json', {'batch_sha256': batch_sha, 'human_signoff': False,
        'seed': policy['sampling_seed'], 'sample_per_stratum': policy['sample_per_stratum'],
        'samples': [{k: f[k] for k in ('id','case_id','field','stratum','machine_status')} for f in batch['fields'] if f['sampled']]})
    summary = {**batch['summary'], 'batch_sha256': batch_sha,
        'policy_id': policy['policy_id'], 'policy_sha256': batch['policy_sha256'],
        'source_comparison_sha256': batch['source_comparison_sha256'],
        'human_signoff': False, 'ai_visual_drafts_used_for_quality_decisions': False,
        'index_engine': 'SQLite FTS5 trigram; short-query substring fallback',
        'allowed_use': policy['allowed_use']}
    write(output/'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=ROOT/'var/pharmacopoeia_dual_review_margin_fixed')
    parser.add_argument('--policy', type=Path, default=ROOT/'eval/pharmacopoeia_qc_policy_v1.json')
    parser.add_argument('--out', type=Path, default=ROOT/'var/pharmacopoeia_research')
    parser.add_argument('--prepared', type=Path, default=ROOT/'var/pharmacopoeia_pdf_benchmark/pages')
    parser.add_argument('--rapidocr', type=Path, default=ROOT/'var/pharmacopoeia_pdf_benchmark/rapidocr')
    parser.add_argument('--paddle', type=Path, default=ROOT/'var/pharmacopoeia_pdf_benchmark/paddle_bounded_no_mkldnn')
    args = parser.parse_args()
    build(args.base, args.policy, args.out, args.prepared, {'rapidocr':args.rapidocr,'paddle':args.paddle})
