"""Reproducible local PDF parser comparison; no production ingestion or cloud OCR.

Prepare images once, download models separately, then run inference with Python
network calls blocked. Full OCR and page images belong only in ignored var/.
"""
import argparse
from contextlib import contextmanager
import importlib.metadata
import json
import os
from pathlib import Path
import resource
import socket
import time
from unittest.mock import patch

from scripts.pilot_pharmacopoeia_ocr import digest, normalized

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / 'eval/pharmacopoeia_pdf_benchmark_v1.json'


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


@contextmanager
def offline():
    attempts = []
    def deny(*args, **kwargs):
        attempts.append('blocked')
        raise RuntimeError('Network forbidden during PDF inference; download models separately')
    with patch.object(socket.socket, 'connect', deny), \
         patch.object(socket.socket, 'connect_ex', deny), \
         patch.object(socket.socket, 'sendto', deny), \
         patch.object(socket, 'getaddrinfo', deny):
        yield attempts


def prepare(suite, destination):
    import pymupdf
    destination.mkdir(parents=True, exist_ok=True)
    sources = json.loads((ROOT / suite['source_manifest']).read_text())['documents']
    for source in sources.values():
        if digest(ROOT / source['path']) != source['sha256']:
            raise ValueError('PDF source fingerprint changed')
    pages = []
    for spec in suite['pages']:
        source = sources[spec['document']]
        key = f"{spec['document']}-{spec['pdf_page']}"
        with pymupdf.open(ROOT / source['path']) as document:
            page = document[spec['pdf_page'] - 1]
            image = destination / f'{key}.png'
            page.get_pixmap(dpi=suite['render_dpi'], alpha=False).save(image)
            pages.append({**spec, 'key': key, 'image': image.name,
                          'image_sha256': digest(image), 'source_sha256': source['sha256'],
                          'pdf_width': page.rect.width, 'pdf_height': page.rect.height})
    if not all(digest(ROOT / s['path']) == s['sha256'] for s in sources.values()):
        raise RuntimeError('PDF source changed during rendering')
    result = {'suite_sha256': digest(SUITE), 'render_dpi': suite['render_dpi'],
              'source_manifest_sha256': digest(ROOT / suite['source_manifest']),
              'renderer_version': importlib.metadata.version('PyMuPDF'), 'pages': pages}
    write_json(destination / 'prepared.json', result)
    return result


def make_engine(name):
    if name == 'rapidocr':
        import onnxruntime
        onnxruntime.disable_telemetry_events()
        from rapidocr_onnxruntime import RapidOCR
        return RapidOCR(intra_op_num_threads=2, inter_op_num_threads=2,
                        det_limit_side_len=960, max_side_len=4000)
    os.environ.setdefault('PADDLE_PDX_CACHE_HOME', str(ROOT/'var/pharmacopoeia_pdf_benchmark/model_cache'))
    os.environ.setdefault('PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK', 'True')
    from paddleocr import PPStructureV3
    return PPStructureV3(device='cpu', cpu_threads=2, enable_mkldnn=False,
                         text_detection_model_name='PP-OCRv5_server_det',
                         text_recognition_model_name='PP-OCRv5_server_rec',
                         text_det_limit_side_len=960, text_det_limit_type='max',
                         text_recognition_batch_size=4,
                         use_doc_orientation_classify=False, use_doc_unwarping=False,
                         use_textline_orientation=False, use_table_recognition=False,
                         use_formula_recognition=False, use_seal_recognition=False,
                         use_chart_recognition=False)


def model_fingerprints(name):
    if name == 'rapidocr':
        import rapidocr_onnxruntime
        directory = Path(rapidocr_onnxruntime.__file__).parent/'models'
    else:
        directory = Path(os.environ['PADDLE_PDX_CACHE_HOME'])
    return {str(path.relative_to(directory)): digest(path) for path in sorted(directory.rglob('*'))
            if path.is_file() and '.cache' not in path.parts and
            path.suffix in {'.onnx', '.json', '.yml', '.yaml', '.pdiparams', '.pdmodel'}}


def recognize(name, engine, image, metadata):
    import numpy as np
    from PIL import Image
    # RapidOCR 1.x consumes BGR arrays; Paddle receives the exact same PNG file.
    rgb = np.asarray(Image.open(image).convert('RGB'))
    height, width = rgb.shape[:2]
    sx, sy = metadata['pdf_width'] / width, metadata['pdf_height'] / height
    lines, blocks = [], []
    def line(text, score, box, offset=0):
        points = [[(float(x) + offset) * sx, float(y) * sy] for x, y in box]
        return {'text': str(text), 'score': float(score), 'pdf_box': points}
    if name == 'rapidocr':
        # Match the previous engineering baseline: fixed half-page columns.
        for left, right in [(0, width // 2), (width // 2, width)]:
            output, _ = engine(rgb[:, left:right, ::-1].copy())
            values = [line(text, score, box, left) for box, text, score in output or []]
            values.sort(key=lambda value: min(y for _, y in value['pdf_box']))
            lines.extend(values)
    else:
        results = list(engine.predict(input=str(image)))
        if len(results) != 1:
            raise ValueError('Expected exactly one page result')
        raw = results[0].json
        if isinstance(raw, str):
            raw = json.loads(raw)
        raw = raw.get('res', raw)
        ocr = raw['overall_ocr_res']
        boxes = ocr.get('rec_polys', ocr.get('dt_polys'))
        if boxes is None or not (len(boxes) == len(ocr['rec_texts']) == len(ocr['rec_scores'])):
            raise ValueError('OCR coordinates/text length mismatch')
        lines = [line(text, score, box) for text, score, box in
                 zip(ocr['rec_texts'], ocr['rec_scores'], boxes)]
        # Preserve the parser's order, separately from OCR detection order.
        for block in raw['parsing_res_list']:
            box = block['block_bbox']
            blocks.append({'label': block['block_label'], 'text': block['block_content'],
                           'pdf_bbox': [box[0]*sx, box[1]*sy, box[2]*sx, box[3]*sy]})
    return {'lines': lines, 'blocks': blocks,
            'reading_text': '\n'.join(b['text'] for b in blocks) if name == 'paddle' else
                            '\n'.join(item['text'] for item in lines)}


def fragment_checks(outputs, reference):
    """Legacy region checks only; never full-text accuracy or reading-order scores."""
    checks = []
    for case in reference['cases']:
        selected = []
        required = {f"{s['document']}-{s['pdf_page']}" for s in case['segments']}
        if not required.issubset(outputs):
            checks.append({'id': case['id'], 'status': 'not_run'})
            continue
        for segment in case['segments']:
            page = outputs[f"{segment['document']}-{segment['pdf_page']}"]
            segment_lines = []
            for item in page['lines']:
                x = sum(p[0] for p in item['pdf_box']) / len(item['pdf_box'])
                y = sum(p[1] for p in item['pdf_box']) / len(item['pdf_box'])
                column = 'left' if x < page['pdf_width']/2 else 'right'
                if column == segment['column'] and segment['y_range'][0] <= y/page['pdf_height'] <= segment['y_range'][1]:
                    segment_lines.append(item)
            selected.extend(sorted(segment_lines, key=lambda item: min(y for _, y in item['pdf_box'])))
        # Sort by segment + y, so this checks recognition, not parser reading order.
        text = normalized(''.join(item['text'] for item in selected))
        checks.append({'id': case['id'], 'status': 'executed', 'fields': {
            field: normalized(value) in text for field, value in case['expected_fragments'].items()}})
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', choices=['prepare', 'rapidocr', 'paddle'], required=True)
    parser.add_argument('--prepared', type=Path, default=ROOT/'var/pharmacopoeia_pdf_benchmark/pages')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--download-models', action='store_true')
    args = parser.parse_args()
    suite = json.loads(SUITE.read_text())
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive')
    if args.engine == 'prepare':
        if args.download_models:
            parser.error('prepare does not download models')
        print(json.dumps({'prepared_pages': len(prepare(suite, args.prepared)['pages'])}))
        return
    # The download-only operation never opens PDF files or page images.
    if args.download_models:
        make_engine(args.engine)
        print('Model initialization/download complete; no PDF inference performed.')
        return
    prepared = json.loads((args.prepared/'prepared.json').read_text())
    if prepared['suite_sha256'] != digest(SUITE) or prepared['source_manifest_sha256'] != digest(ROOT/suite['source_manifest']):
        raise ValueError('Prepared inputs no longer match frozen manifests')
    run_name = 'paddle_bounded_no_mkldnn' if args.engine == 'paddle' else args.engine
    destination = args.out or ROOT/f'var/pharmacopoeia_pdf_benchmark/{run_name}'
    destination.mkdir(parents=True, exist_ok=True)
    if (destination/'report.json').exists():
        raise FileExistsError('Use a fresh output directory to preserve experiment history')
    outputs = {}
    report = {'engine': args.engine, 'suite_sha256': digest(SUITE),
              'prepared_sha256': digest(args.prepared/'prepared.json'),
              'code_sha256': digest(Path(__file__)), 'status': 'running', 'pages': [],
              'human_signoff': False, 'ocr_character_accuracy': None,
              'reading_order_accuracy': None, 'business_task_completion_rate': None,
              'settings': {'device': 'cpu', 'threads': 2, 'render_dpi': prepared['render_dpi'],
                           'rapidocr_fixed_half_columns': args.engine == 'rapidocr',
                           'paddle_detection_long_side_max': 960 if args.engine == 'paddle' else None,
                           'paddle_recognition_batch_size': 4 if args.engine == 'paddle' else None,
                           'paddle_enable_mkldnn': False,
                           'optional_table_formula_chart_seal': False}}
    packages = ['paddleocr', 'paddlepaddle', 'paddlex'] if args.engine == 'paddle' else ['rapidocr-onnxruntime', 'onnxruntime']
    report['versions'] = {name: importlib.metadata.version(name) for name in packages}
    try:
        with offline() as attempts:
            started = time.perf_counter()
            engine = make_engine(args.engine)
            report['initialization_seconds'] = round(time.perf_counter()-started, 3)
            report['model_fingerprints'] = model_fingerprints(args.engine)
            for page in prepared['pages'][:args.limit]:
                image = args.prepared/page['image']
                if digest(image) != page['image_sha256']:
                    raise ValueError('Prepared page image changed')
                started = time.perf_counter()
                result = recognize(args.engine, engine, image, page)
                seconds = round(time.perf_counter()-started, 3)
                outputs[page['key']] = {**page, **result}
                write_json(destination/f"{page['key']}.json", outputs[page['key']])
                report['pages'].append({'key': page['key'], 'seconds': seconds,
                                        'lines': len(result['lines']), 'blocks': len(result['blocks'])})
                write_json(destination/'report.json', report)
                print(page['key'], seconds, 'seconds', flush=True)
        reference = json.loads((ROOT/suite['source_manifest']).read_text())
        report['blocked_network_attempts'] = len(attempts)
        report['legacy_fragment_checks'] = fragment_checks(outputs, reference)
        report['status'] = 'completed' if len(outputs) == len(prepared['pages']) else 'partial'
    except Exception as error:
        report['status'] = 'failed'
        report['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        report['completed_pages'] = len(outputs)
        report['total_pages'] = len(prepared['pages'])
        report['page_processing_seconds'] = round(sum(p['seconds'] for p in report['pages']), 3)
        report['process_peak_rss_mib'] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
        write_json(destination/'report.json', report)


if __name__ == '__main__':
    main()
