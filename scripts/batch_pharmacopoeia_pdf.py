"""Offline, resumable OCR pilot. Outputs remain unreviewed and private under var/."""
import argparse
import fcntl
import importlib.metadata
import json
from pathlib import Path
import time

from scripts.benchmark_pharmacopoeia_pdf import make_engine, model_fingerprints, offline, recognize
from tcm.pharmacopoeia import digest
from tcm.pharmacopoeia_segmentation import fingerprint, segment_pages, page_lines

ROOT = Path(__file__).resolve().parents[1]
HERB_PROFILE = 'herb_monographs_two_column_v1'
# Replay only the frozen nine-page pilot without a chapter declaration.
LEGACY_PILOT_FINGERPRINT = 'ca690fb2e50cf9cc8a083eb29bf3f22d79d21c15ec6b33e3249df802832dce5f'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def validate_plan(plan):
    if plan.get('schema_version') != 1 or plan['render_dpi'] != 240 or not plan['pages']:
        raise ValueError('Unsupported or empty plan')
    profile = plan.get('chapter_profile')
    if profile is None:
        if fingerprint(plan) != LEGACY_PILOT_FINGERPRINT:
            raise ValueError('New plans must declare an inspected chapter_profile')
    elif profile != HERB_PROFILE:
        raise ValueError('Chapter profile is not supported by the herb extractor')
    seen = set()
    for spec in plan['pages']:
        if (spec.get('chapter_profile', HERB_PROFILE) != HERB_PROFILE or
            spec.get('layout', 'two_column') != 'two_column'):
            raise ValueError('Mixed chapter or layout requires a separate inspected extraction plan')
        key = (spec['document'], spec['pdf_page'])
        if key in seen or type(spec['pdf_page']) is not int or spec['pdf_page'] < 1:
            raise ValueError('Duplicate or invalid physical page')
        seen.add(key)
        if spec['document'] not in plan['documents']:
            raise ValueError('Unknown document')
    # Ordered blocks avoid accidentally stitching reordered pages.
    for document in {p['document'] for p in plan['pages']}:
        numbers = [p['pdf_page'] for p in plan['pages'] if p['document'] == document]
        if numbers != sorted(numbers):
            raise ValueError('Pages must be ascending per document')


def identity_for(spec, source, recipe_sha256):
    return {'document': spec['document'], 'pdf_page': spec['pdf_page'],
            'source_sha256': source['sha256'], 'recipe_sha256': recipe_sha256}


def load_cache(path, identity, expected_file_sha256=None):
    if expected_file_sha256 and digest(path) != expected_file_sha256:
        raise ValueError('Previously completed cache file changed')
    data = read(path)
    if data['identity'] != identity or fingerprint(data['result']) != data['result_sha256']:
        raise ValueError('OCR cache identity or content mismatch')
    result = data['result']
    if any(result[k] != identity[k] for k in ('document', 'pdf_page', 'source_sha256')):
        raise ValueError('OCR result source mismatch')
    page_lines({**result, 'page_result_sha256': data['result_sha256']})
    return data


def import_result(spec, source, directory, prepared_directory, versions):
    key = f"{spec['document']}-{spec['pdf_page']}"
    path, report_path = directory / f'{key}.json', directory / 'report.json'
    if digest(path) != spec['reuse_result_sha256']:
        raise ValueError('Frozen imported OCR result changed')
    report, prepared = read(report_path), read(prepared_directory / 'prepared.json')
    settings = report['settings']
    if (report['engine'] != 'rapidocr' or report['status'] != 'completed' or
        report['versions'] != {k: versions[k] for k in report['versions']} or
        not settings['rapidocr_fixed_half_columns'] or settings['render_dpi'] != 240 or
        settings['threads'] != 2 or settings['device'] != 'cpu' or
        report['prepared_sha256'] != digest(prepared_directory / 'prepared.json') or
        key not in {p['key'] for p in report['pages']}):
        raise ValueError('Imported OCR profile or preparation mismatch')
    matches = [p for p in prepared['pages'] if p['key'] == key]
    if len(matches) != 1:
        raise ValueError('Missing unique imported page metadata')
    meta, result = matches[0], read(path)
    if (any(result[k] != v for k, v in meta.items()) or meta['source_sha256'] != source['sha256'] or
        meta['pdf_page'] != spec['pdf_page'] or meta['document'] != spec['document'] or
        digest(prepared_directory / meta['image']) != meta['image_sha256']):
        raise ValueError('Imported image or source evidence mismatch')
    return result, {'method': 'frozen_prior_rapidocr_result', 'raw_file_sha256': digest(path),
                    'report_sha256': digest(report_path), 'models_at_original_run': 'not_recorded',
                    'note': 'Current model fingerprints do not prove historical model identity.'}


def run(plan_path, out, reuse_dir=None, prepared_dir=None, max_new_pages=None):
    """One writer per output directory; interrupted processes release the OS lock."""
    out.mkdir(parents=True, exist_ok=True)
    with (out / '.writer.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('Another batch process is using this output directory') from error
        return _run(plan_path, out, reuse_dir, prepared_dir, max_new_pages)


def _run(plan_path, out, reuse_dir=None, prepared_dir=None, max_new_pages=None):
    plan = read(plan_path)
    validate_plan(plan)
    if max_new_pages is not None and max_new_pages < 0:
        raise ValueError('max-new-pages must be nonnegative')
    # Verify input identity before writing data or initializing inference.
    sources = {p['document'] for p in plan['pages']}
    for label in sources:
        if digest(ROOT / plan['documents'][label]['path']) != plan['documents'][label]['sha256']:
            raise ValueError('PDF source changed')
    versions = {p: importlib.metadata.version(p) for p in
                ('rapidocr-onnxruntime', 'onnxruntime', 'PyMuPDF', 'numpy', 'Pillow', 'opencv-python')}
    recipe = {'schema_version': 1, 'engine': 'rapidocr', 'versions': versions,
              'model_files_sha256': model_fingerprints('rapidocr'), 'render_dpi': plan['render_dpi'],
              'inference_code_sha256': digest(ROOT / 'scripts/benchmark_pharmacopoeia_pdf.py'),
              'renderer_recipe': 'pymupdf_get_pixmap_dpi_alpha_false_v1',
              'settings': {'threads': 2, 'det_limit_side_len': 960, 'max_side_len': 4000,
                           'columns': 'fixed_halves', 'input_channels': 'BGR'}}
    identity = {'plan_sha256': digest(plan_path), 'recipe_sha256': fingerprint(recipe)}
    state_path = out / 'state.json'
    state = read(state_path) if state_path.exists() else {**identity, 'page_receipts': {}}
    if any(state[k] != v for k, v in identity.items()):
        raise ValueError('Run plan or OCR recipe changed; use a fresh output directory')
    results, errors, pending, counts = [], [], [], {'cached_pages': 0, 'imported_pages': 0, 'new_ocr_pages': 0}
    engine = None
    started = time.monotonic()
    with offline() as network_attempts:
        for spec in plan['pages']:
            source = plan['documents'][spec['document']]
            key = f"{spec['document']}-{spec['pdf_page']}"
            page_identity = identity_for(spec, source, identity['recipe_sha256'])
            cache = out / 'ocr_cache' / f'{fingerprint(page_identity)}.json'
            if cache.exists():
                data = load_cache(cache, page_identity, state['page_receipts'].get(key))
                counts['cached_pages'] += 1
            else:
                if key in state['page_receipts']:
                    raise ValueError('Previously completed cache file missing')
                can_import = reuse_dir is not None and 'reuse_result_sha256' in spec
                if not can_import and max_new_pages is not None and counts['new_ocr_pages'] >= max_new_pages:
                    pending.append(key)
                    continue
                # Source/config/import-integrity errors are fatal; transient native OCR failures are resumable.
                if can_import:
                    result, provenance = import_result(spec, source, reuse_dir, prepared_dir, versions)
                    counts['imported_pages'] += 1
                else:
                    import pymupdf
                    try:
                        image = out / 'pages' / f'{key}.png'
                        image.parent.mkdir(parents=True, exist_ok=True)
                        with pymupdf.open(ROOT / source['path']) as document:
                            page = document[spec['pdf_page'] - 1]
                            page.get_pixmap(dpi=plan['render_dpi'], alpha=False).save(image)
                            metadata = {**spec, 'key': key, 'source_sha256': source['sha256'],
                                        'pdf_width': page.rect.width, 'pdf_height': page.rect.height,
                                        'image': str(image.relative_to(out)), 'image_sha256': digest(image)}
                        if engine is None:
                            engine = make_engine('rapidocr')
                        counts['new_ocr_pages'] += 1
                        result = {**metadata, **recognize('rapidocr', engine, image, metadata)}
                        provenance = {'method': 'native_offline_rapidocr', 'recipe_sha256': identity['recipe_sha256']}
                    except Exception as error:
                        errors.append({'key': key, 'error_type': type(error).__name__, 'message': str(error)})
                        continue
                data = {'identity': page_identity, 'result': result, 'result_sha256': fingerprint(result),
                        'provenance': provenance}
                page_lines({**result, 'page_result_sha256': data['result_sha256']})
                atomic_json(cache, data)
                load_cache(cache, page_identity)
            state['page_receipts'][key] = digest(cache)
            atomic_json(state_path, state)
            results.append({**data['result'], 'printed_page': spec.get('printed_page'),
                            'page_result_sha256': data['result_sha256']})
        for label in sources:
            if digest(ROOT / plan['documents'][label]['path']) != plan['documents'][label]['sha256']:
                raise ValueError('PDF source changed during run')
    parsed = segment_pages(results)
    parsed.update(inputs=identity, parser_code_sha256=digest(ROOT / 'tcm/pharmacopoeia_segmentation.py'),
                  field_extractor_code_sha256=digest(ROOT / 'tcm/pharmacopoeia.py'))
    atomic_json(out / 'entries.json', parsed)
    summary = {**identity, **counts, 'kind': 'bounded_batch_ocr_run',
               'runner_code_sha256': digest(Path(__file__)),
               'status': 'failed_pages' if errors else 'partial' if pending else 'completed',
               'completed_pages': len(results), 'planned_pages': len(plan['pages']),
               'pending_pages': pending, 'failed_pages': errors, 'segmentation': parsed['metrics'],
               'entries_sha256': digest(out / 'entries.json'), 'human_signoff': False,
               'blocked_python_network_attempts': len(network_attempts),
               'elapsed_seconds': round(time.monotonic() - started, 3),
               'ocr_character_accuracy': None, 'boundary_accuracy': None}
    atomic_json(out / 'recipe.json', recipe)
    atomic_json(out / 'summary.json', summary)
    runs = out / 'runs'
    number = 1
    while (runs / f'{number:04}.json').exists():
        number += 1
    atomic_json(runs / f'{number:04}.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, default=ROOT / 'eval/pharmacopoeia_batch_pilot_v1.json')
    parser.add_argument('--out', type=Path, default=ROOT / 'var/pharmacopoeia_batch_pilot')
    parser.add_argument('--reuse-ocr-dir', type=Path)
    parser.add_argument('--reuse-prepared', type=Path)
    parser.add_argument('--max-new-pages', type=int)
    args = parser.parse_args()
    if (args.reuse_ocr_dir is None) != (args.reuse_prepared is None):
        parser.error('Both reuse options are required together')
    summary = run(args.plan, args.out, args.reuse_ocr_dir, args.reuse_prepared, args.max_new_pages)
    print(json.dumps(summary, ensure_ascii=False))
    if summary['failed_pages']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
