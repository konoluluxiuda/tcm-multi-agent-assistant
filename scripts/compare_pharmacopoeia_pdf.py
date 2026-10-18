"""Compare local runs without publishing OCR text or inventing accuracy scores."""
import argparse
import json
from pathlib import Path

from scripts.benchmark_pharmacopoeia_pdf import ROOT, SUITE, write_json
from scripts.pilot_pharmacopoeia_ocr import digest, normalized


def summarize(directories):
    reference = json.loads((ROOT/'eval/pharmacopoeia_ocr_sample_v1.json').read_text())
    flags = json.loads((ROOT/'eval/pharmacopoeia_ocr_flags_v1.json').read_text())
    summaries, identities, prepared_hashes = [], [], []
    for directory in directories:
        report_path = directory/'report.json'
        report = json.loads(report_path.read_text())
        prepared_hashes.append(report['prepared_sha256'])
        if report['suite_sha256'] != digest(SUITE):
            raise ValueError('Run does not match current frozen suite')
        if report['status'] != 'completed' or report['completed_pages'] != report['total_pages']:
            raise ValueError('Full comparison requires completed runs; partial/failure is not a pass')
        pages = {p['key']: json.loads((directory/f"{p['key']}.json").read_text()) for p in report['pages']}
        identities.append({key: (p['image_sha256'], p['source_sha256'], p['pdf_page']) for key, p in pages.items()})
        posthoc = []
        for index, flag in enumerate(flags):
            document, number, column = flag['source_key'].split('-')
            page = pages[f'{document}-{number}']
            case = next(c for c in reference['cases'] if c['id'] == flag['case_id'])
            segments = [s for s in case['segments'] if s['document'] == document and
                        s['pdf_page'] == int(number) and s['column'] == column]
            selected = []
            for line in page['lines']:
                box = line['pdf_box']
                x = sum(p[0] for p in box)/len(box)/page['pdf_width']
                y = sum(p[1] for p in box)/len(box)/page['pdf_height']
                if ('left' if x < .5 else 'right') == column and any(s['y_range'][0] <= y <= s['y_range'][1] for s in segments):
                    selected.append(line)
            text = normalized(''.join(l['text'] for l in sorted(selected, key=lambda l: min(p[1] for p in l['pdf_box']))))
            posthoc.append({'flag_index': index, 'case_id': flag['case_id'],
                            'reference_fragment_present_in_region': normalized(flag['visual_reference']) in text,
                            'previous_error_fragment_present_in_region': normalized(flag['observed_fragment']) in text})
        fields = [value for case in report['legacy_fragment_checks'] if case['status'] == 'executed'
                  for value in case['fields'].values()]
        summaries.append({'engine': report['engine'], 'report_sha256': digest(report_path),
                          'runner_sha256': report['code_sha256'], 'versions': report['versions'],
                          'settings': report['settings'],
                          'completed_pages': report['completed_pages'],
                          'page_processing_seconds': round(sum(p['seconds'] for p in report['pages']), 3),
                          'process_peak_rss_mib': report.get('process_peak_rss_mib'),
                          'blocked_network_attempts': report.get('blocked_network_attempts'),
                          'legacy_fragments': {'matched': sum(fields), 'total': len(fields)},
                          'posthoc_flags': posthoc,
                          'page_statistics': report['pages']})
    if len({r['engine'] for r in summaries}) != len(summaries):
        raise ValueError('Expected distinct engines')
    if any(identity != identities[0] for identity in identities[1:]):
        raise ValueError('Compared runs used different page images or source PDFs')
    if len(set(prepared_hashes)) != 1:
        raise ValueError('Compared runs used different prepared input manifests')
    return {'kind': 'engineering_parser_comparison', 'suite_sha256': digest(SUITE),
            'input_identity_verified': True, 'human_signoff': False,
            'source_pdf_authenticity_verified': False,
            'reference_status': 'ai_visual_pending_human_review',
            'ocr_character_accuracy': None, 'reading_order_accuracy': None,
            'business_task_completion_rate': None, 'runs': summaries,
            'limitations': ['Purposive sample includes blank pages and duplicate content across sources.',
                            'Single-pass timing with different pipelines and no controlled background workload; not a throughput benchmark.',
                            'Posthoc token presence in a herb region is not proof of correction in the same sentence.',
                            'Compare complete pipelines, not an isolated recognition model.',
                            'First RapidOCR run did not record peak RSS.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rapidocr', type=Path, default=ROOT/'var/pharmacopoeia_pdf_benchmark/rapidocr')
    parser.add_argument('--paddle', type=Path, default=ROOT/'var/pharmacopoeia_pdf_benchmark/paddle_bounded_no_mkldnn')
    parser.add_argument('--out', type=Path, default=ROOT/'var/pharmacopoeia_pdf_benchmark/comparison.json')
    args = parser.parse_args()
    result = summarize([args.rapidocr, args.paddle])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.out, result)
    print(json.dumps([{'engine': r['engine'], 'pages': r['completed_pages'],
                       'fragments': r['legacy_fragments']} for r in result['runs']]))


if __name__ == '__main__':
    main()
