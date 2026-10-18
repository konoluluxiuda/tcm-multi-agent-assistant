"""Rebuild candidates from verified immutable OCR receipts into a fresh directory."""
import argparse
import json
from pathlib import Path

from scripts.batch_pharmacopoeia_pdf import atomic_json, identity_for, load_cache, read, validate_plan
from tcm.pharmacopoeia import digest
from tcm.pharmacopoeia_boundary_eval import evaluate_boundaries
from tcm.pharmacopoeia_segmentation import fingerprint, segment_pages

ROOT = Path(__file__).resolve().parents[1]


def verified_pages(plan_path, batch):
    plan, state, summary, recipe = (read(p) for p in
        (plan_path, batch/'state.json', batch/'summary.json', batch/'recipe.json'))
    validate_plan(plan)
    if (state['plan_sha256'] != digest(plan_path) or fingerprint(recipe) != state['recipe_sha256'] or
        summary['plan_sha256'] != state['plan_sha256'] or summary['recipe_sha256'] != state['recipe_sha256'] or
        summary['status'] != 'completed' or summary['completed_pages'] != len(plan['pages']) or
        summary['failed_pages'] or summary['pending_pages'] or
        digest(batch/'entries.json') != summary['entries_sha256']):
        raise ValueError('Baseline incomplete or identities changed')
    required={f"{p['document']}-{p['pdf_page']}" for p in plan['pages']}
    if set(state['page_receipts']) != required:
        raise ValueError('Page receipt coverage mismatch')
    for label in {p['document'] for p in plan['pages']}:
        source=plan['documents'][label]
        if digest(ROOT/source['path']) != source['sha256']:
            raise ValueError('Source PDF changed')
    pages=[]
    for spec in plan['pages']:
        identity=identity_for(spec,plan['documents'][spec['document']],state['recipe_sha256'])
        key=f"{spec['document']}-{spec['pdf_page']}"
        cache=load_cache(batch/'ocr_cache'/f'{fingerprint(identity)}.json',identity,state['page_receipts'][key])
        result=cache['result']
        if cache['provenance']['method'] != 'native_offline_rapidocr':
            raise ValueError('This rebuild expects native page-image receipts')
        if digest(batch/result['image']) != result['image_sha256']:
            raise ValueError('Source image changed')
        pages.append({**result,'printed_page':spec.get('printed_page'),'page_result_sha256':cache['result_sha256']})
    return plan,state,summary,pages


def rebuild(plan_path,batch,out,reference_path=None):
    if out.exists():
        raise FileExistsError('Fresh output directory required; baseline is read-only')
    plan,state,baseline,pages=verified_pages(plan_path,batch)
    reference=read(reference_path) if reference_path else None
    if reference and [(p['document'],p['pdf_page']) for p in reference['pages']] != [
        (p['document'],p['pdf_page']) for p in plan['pages']]:
        raise ValueError('Reference coverage mismatch')
    parsed=segment_pages(pages)
    parsed.update(inputs={'plan_sha256':state['plan_sha256'],'recipe_sha256':state['recipe_sha256']},
                  parser_code_sha256=digest(ROOT/'tcm/pharmacopoeia_segmentation.py'),
                  field_extractor_code_sha256=digest(ROOT/'tcm/pharmacopoeia.py'))
    report=evaluate_boundaries(reference,parsed) if reference else None
    if report:
        report.update(evaluation_role='known_failure_regression',independent_validation=False,
                      reference_sha256=digest(reference_path),baseline_entries_sha256=baseline['entries_sha256'])
    out.mkdir(parents=True,exist_ok=False)
    atomic_json(out/'entries.json',parsed)
    if report:atomic_json(out/'regression_report.json',report)
    summary={'kind':'verified_ocr_resegmentation','status':'completed','human_signoff':False,
             'source_batch':str(batch),'source_plan_sha256':digest(plan_path),
             'source_state_sha256':digest(batch/'state.json'),'source_recipe_sha256':state['recipe_sha256'],
             'source_entries_sha256':baseline['entries_sha256'],'new_ocr_pages':0,'verified_pages':len(pages),
             'parser_code_sha256':parsed['parser_code_sha256'],'field_extractor_code_sha256':parsed['field_extractor_code_sha256'],
             'entries_sha256':digest(out/'entries.json'),'segmentation':parsed['metrics'],
             'source_unresolved_entries':sum(e['source_status']=='unresolved_missing_source_opener' for e in parsed['entries']),
             'requires_source_review':sum(e['review_status']=='requires_source_review' for e in parsed['entries']),
             'boundary_regression_status':report['status'] if report else 'not_run',
             'resegmenter_code_sha256':digest(Path(__file__)),
             'boundary_regression_counts':report['counts'] if report else None}
    if digest(batch/'entries.json') != baseline['entries_sha256']:
        raise ValueError('Baseline changed during rebuild')
    atomic_json(out/'summary.json',summary)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,default=ROOT/'eval/pharmacopoeia_boundary_validation_v1.json')
    parser.add_argument('--batch',type=Path,default=ROOT/'var/pharmacopoeia_boundary_validation_v1/batch')
    parser.add_argument('--out',type=Path,default=ROOT/'var/pharmacopoeia_boundary_repair_v3/regression')
    parser.add_argument('--reference',type=Path,default=ROOT/'eval/pharmacopoeia_boundary_visual_v2.json')
    args=parser.parse_args()
    summary=rebuild(args.plan,args.batch,args.out,args.reference)
    print(json.dumps(summary,ensure_ascii=False))
    if summary['boundary_regression_status']=='completed_with_boundary_failures':
        raise SystemExit(2)


if __name__=='__main__':main()
