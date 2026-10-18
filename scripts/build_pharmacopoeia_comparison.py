"""Build offline dual-engine review cards and blank human reference slots."""
import argparse
from collections import Counter
import json
from pathlib import Path

from scripts.compare_pharmacopoeia_pdf import summarize
from tcm.pharmacopoeia import digest
from tcm.pharmacopoeia_comparison import build_comparison, review_template, validate_page, ENGINES

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'var/pharmacopoeia_pdf_benchmark'


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def build(output, rapidocr, paddle, prepared_dir):
    comparison = summarize([rapidocr, paddle])  # Refuse incomplete or mismatched runs.
    suite_path = ROOT/'eval/pharmacopoeia_ocr_sample_v1.json'
    suite = json.loads(suite_path.read_text())
    prepared_path = prepared_dir/'prepared.json'
    prepared = json.loads(prepared_path.read_text())
    if prepared['suite_sha256'] != comparison['suite_sha256'] or prepared['source_manifest_sha256'] != digest(suite_path):
        raise ValueError('Stale prepared manifest')
    expected = {p['key']: p for p in prepared['pages']}
    for source in suite['documents'].values():
        if digest(ROOT/source['path']) != source['sha256']:
            raise ValueError('Source PDF changed')
    engines, inputs = {}, {}
    for name, directory in zip(ENGINES, (rapidocr, paddle)):
        report = json.loads((directory/'report.json').read_text())
        if report['engine'] != name or report['prepared_sha256'] != digest(prepared_path):
            raise ValueError('Engine or prepared manifest mismatch')
        pages = {}
        for key, spec in expected.items():
            path = directory/f'{key}.json'
            page = json.loads(path.read_text())
            validate_page(page, spec)
            page['result_sha256'] = digest(path)
            pages[key] = page
        engines[name] = pages
        inputs[name] = {'report_sha256': digest(directory/'report.json'),
                        'page_results': {key: p['result_sha256'] for key, p in pages.items()},
                        'versions': report['versions'], 'settings': report['settings']}
    data = build_comparison(suite, engines)
    data.update(inputs=inputs, manual_regions_sha256=digest(suite_path),
                prepared_sha256=digest(prepared_path),
                extractor_sha256=digest(ROOT/'tcm/pharmacopoeia.py'),
                comparison_code_sha256=digest(ROOT/'tcm/pharmacopoeia_comparison.py'))
    output.mkdir(parents=True, exist_ok=True)
    assets = output/'assets'
    assets.mkdir(exist_ok=True)
    import pymupdf
    crops = 0
    for record in data['records']:
        for field in record['fields']:
            groups = {}
            for engine in ENGINES:
                for occurrence in field['engines'][engine]['occurrences']:
                    for line in occurrence['evidence']:
                        groups.setdefault((line['document'], line['pdf_page']), {})[
                            (engine, line['line_index'])] = (engine, line)
            for (label, number), evidence in groups.items():
                with pymupdf.open(ROOT/suite['documents'][label]['path']) as pdf:
                    page = pdf[number-1]
                    clip = None
                    for engine, line in evidence.values():
                        xs, ys = zip(*line['pdf_box'])
                        box = pymupdf.Rect(min(xs), min(ys), max(xs), max(ys))
                        if not page.rect.contains(box):
                            raise ValueError('Evidence outside source PDF')
                        clip = box if clip is None else clip | box
                        annotation = page.add_rect_annot(box)
                        annotation.set_colors(stroke=(.85, .1, .1) if engine == 'rapidocr' else (.1, .25, .85))
                        annotation.set_border(width=.5)
                        annotation.update()
                    image = f'{record["id"]}-{field["key"]}-{label}-{number}.png'
                    page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=(clip+(-10,-10,10,10)) & page.rect).save(assets/image)
                    field['previews'].append({'image': 'assets/'+image, 'document': label,
                        'pdf_page': number, 'printed_page': suite['documents'][label]['printed_pages'][str(number)],
                        'source_sha256': suite['documents'][label]['sha256']})
                    crops += 1
        # Whole manually selected regions remain visible even if neither engine found a field.
        record['region_previews'] = []
        for i, region in enumerate(record['regions']):
            label, number = region['document'], region['pdf_page']
            with pymupdf.open(ROOT/suite['documents'][label]['path']) as pdf:
                page = pdf[number-1]
                w, h = page.rect.width, page.rect.height
                x0 = 0 if region['column'] == 'left' else w/2
                clip = pymupdf.Rect(x0, region['y_range'][0]*h, x0+w/2, region['y_range'][1]*h)
                image = f'{record["id"]}-region-{i}.png'
                page.get_pixmap(matrix=pymupdf.Matrix(2,2), clip=clip).save(assets/image)
                record['region_previews'].append({'image': 'assets/'+image, **region,
                    'printed_page': suite['documents'][label]['printed_pages'][str(number)],
                    'source_sha256': suite['documents'][label]['sha256']})
    unchanged = all(digest(ROOT/s['path']) == s['sha256'] for s in suite['documents'].values())
    if not unchanged:
        raise RuntimeError('Source PDF changed during rendering')
    write(output/'comparison.json', data)
    data['artifact_sha256'] = digest(output/'comparison.json')
    write(output/'review_template.json', review_template(data))
    (output/'index.html').write_text((ROOT/'tcm/pharmacopoeia_comparison.html').read_text(), encoding='utf-8')
    counts = Counter(f['comparison'] for r in data['records'] for f in r['fields'])
    summary = {'kind': data['kind'], 'records': len(data['records']), 'field_slots': sum(counts.values()),
               'comparison_counts': dict(counts), 'source_crops': crops,
               'region_crops': sum(len(r['region_previews']) for r in data['records']),
               'excluded_margin_lines': sum(len(items) for r in data['records'] for items in r['excluded_margin_lines'].values()),
               'comparison_sha256': data['artifact_sha256'],
               'review_template_sha256': digest(output/'review_template.json'),
               'source_pdfs_unchanged': unchanged, 'human_signoff': False,
               'approved_fields': 0, 'reference_transcriptions_completed': 0,
               'input_reports': {name: value['report_sha256'] for name, value in inputs.items()}}
    write(output/'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT/'var/pharmacopoeia_dual_review')
    parser.add_argument('--rapidocr', type=Path, default=BASE/'rapidocr')
    parser.add_argument('--paddle', type=Path, default=BASE/'paddle_bounded_no_mkldnn')
    parser.add_argument('--prepared', type=Path, default=BASE/'pages')
    args = parser.parse_args()
    build(args.out, args.rapidocr, args.paddle, args.prepared)
