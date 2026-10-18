"""Verify frozen inputs and page receipts before a boundary-only evaluation."""
import argparse
import json
from pathlib import Path

from scripts.batch_pharmacopoeia_pdf import atomic_json, load_cache, identity_for, read
from tcm.pharmacopoeia import digest
from tcm.pharmacopoeia_segmentation import fingerprint, segment_pages
from tcm.pharmacopoeia_boundary_eval import evaluate_boundaries

ROOT = Path(__file__).resolve().parents[1]


def evaluate(plan_path, reference_path, freeze_path, batch, out):
    freeze, plan, reference = read(freeze_path), read(plan_path), read(reference_path)
    if (digest(plan_path) != freeze['plan_sha256'] or
        digest(reference_path) != freeze['visual_reference_sha256']):
        raise ValueError('Frozen plan or visual reference changed')
    for file, expected in freeze['sources_sha256'].items():
        if digest(ROOT / file) != expected:
            raise ValueError('Frozen extraction code changed: ' + file)
    for label in {p['document'] for p in plan['pages']}:
        source = plan['documents'][label]
        if digest(ROOT / source['path']) != source['sha256']:
            raise ValueError('Frozen PDF source changed')
    state, summary, recipe = (read(batch / name) for name in ('state.json', 'summary.json', 'recipe.json'))
    if (state['plan_sha256'] != freeze['plan_sha256'] or summary['status'] != 'completed' or
        summary['completed_pages'] != len(plan['pages']) or summary['failed_pages'] or summary['pending_pages'] or
        fingerprint(recipe) != state['recipe_sha256'] or
        summary['plan_sha256'] != state['plan_sha256'] or summary['recipe_sha256'] != state['recipe_sha256']):
        raise ValueError('Batch incomplete or receipt identities inconsistent')
    reference_keys = [(p['document'], p['pdf_page']) for p in reference['pages']]
    if reference_keys != [(p['document'], p['pdf_page']) for p in plan['pages']]:
        raise ValueError('Reference and batch page coverage differ')
    pages = []
    for spec in plan['pages']:
        key = f"{spec['document']}-{spec['pdf_page']}"
        identity = identity_for(spec, plan['documents'][spec['document']], state['recipe_sha256'])
        path = batch / 'ocr_cache' / f'{fingerprint(identity)}.json'
        cache = load_cache(path, identity, state['page_receipts'][key])
        page = cache['result']
        if cache['provenance']['method'] != 'native_offline_rapidocr':
            raise ValueError('Validation expects fresh native OCR, not a prior development result')
        if digest(batch / page['image']) != page['image_sha256']:
            raise ValueError('OCR evidence image changed')
        visual = reference['pages'][len(pages)]
        visual_path = batch.parent / 'visual' / f'{key}.png'
        if digest(visual_path) != visual['image_sha256'] or visual['printed_page'] != spec['printed_page']:
            raise ValueError('Visual evidence or page label changed')
        pages.append({**page, 'printed_page': spec.get('printed_page'),
                      'page_result_sha256': cache['result_sha256']})
    reconstructed = segment_pages(pages)
    reconstructed.update(inputs={'plan_sha256': state['plan_sha256'], 'recipe_sha256': state['recipe_sha256']},
                         parser_code_sha256=digest(ROOT / 'tcm/pharmacopoeia_segmentation.py'),
                         field_extractor_code_sha256=digest(ROOT / 'tcm/pharmacopoeia.py'))
    if reconstructed != read(batch / 'entries.json') or digest(batch / 'entries.json') != summary['entries_sha256']:
        raise ValueError('Entries no longer match frozen extraction of original page receipts')
    report = evaluate_boundaries(reference, reconstructed)
    report.update(engineering_integrity_status='passed',
                  inputs={'freeze_sha256': digest(freeze_path), 'plan_sha256': digest(plan_path),
                          'reference_sha256': digest(reference_path), 'entries_sha256': summary['entries_sha256'],
                          'recipe_sha256': state['recipe_sha256'],
                          'evaluator_code_sha256': digest(Path(__file__)),
                          'evaluation_module_sha256': digest(ROOT / 'tcm/pharmacopoeia_boundary_eval.py')})
    if out.exists():
        raise FileExistsError('Use a fresh evaluation output file to preserve experiment history')
    atomic_json(out, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, default=ROOT / 'eval/pharmacopoeia_boundary_validation_v1.json')
    parser.add_argument('--reference', type=Path, default=ROOT / 'eval/pharmacopoeia_boundary_visual_v2.json')
    parser.add_argument('--freeze', type=Path, default=ROOT / 'audits/pharmacopoeia_boundary_validation/freeze_v2.json')
    parser.add_argument('--batch', type=Path, default=ROOT / 'var/pharmacopoeia_boundary_validation_v1/batch')
    parser.add_argument('--out', type=Path, default=ROOT / 'audits/pharmacopoeia_boundary_validation/report.json')
    args = parser.parse_args()
    report = evaluate(args.plan, args.reference, args.freeze, args.batch, args.out)
    print(json.dumps({k: report[k] for k in ('status', 'engineering_integrity_status', 'counts')}, ensure_ascii=False))
    # Explicitly fail the boundary gate while retaining the report for diagnosis.
    if report['status'] != 'completed_without_observed_boundary_failures':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
