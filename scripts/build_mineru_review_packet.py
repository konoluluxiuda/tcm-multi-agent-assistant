"""Freeze a risk queue and deterministic control sample, with original-PDF crops.

Requires PyMuPDF and Pillow only for rendering; does not OCR or approve records.
"""
import argparse
import hashlib
import html
import json
from pathlib import Path

from tcm.mineru_pharmacopoeia import digest

ROOT = Path(__file__).resolve().parents[1]
SEED = 'mineru-review-20261017-v1'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def select(entries):
    flagged = [e for e in entries if e['flags']]
    controls = {}
    for part in sorted({e['parts'][0] for e in entries}):
        eligible = [e for e in entries if not e['flags'] and e['parts'][0] == part]
        ranked = sorted(eligible, key=lambda e: hashlib.sha256(f"{SEED}:{e['id']}".encode()).hexdigest())
        for e in ranked[:2]:
            controls[e['id']] = e
    cross = [e for e in entries if not e['flags'] and len(e['parts']) > 1]
    controls.update({e['id']: e for e in cross})
    return flagged, sorted(controls.values(), key=lambda e: e['id'])


def build(base, output):
    import pymupdf as fitz
    from PIL import Image, ImageDraw
    if output.exists():
        raise FileExistsError('Use a fresh directory; prior review artifacts are immutable')
    summary = read(base / 'summary.json')
    for name in ('entries', 'blocks'):
        if digest(base / f'{name}.json') != summary[f'{name}_sha256']:
            raise ValueError(f'{name} changed since build')
    entries = read(base / 'entries.json')['entries']
    blocks = {b['id']: b for b in read(base / 'blocks.json')}
    flagged, controls = select(entries)
    docs = {}
    paths = {'high': ROOT / 'data/《中国药典》2025年版 一部.pdf',
             'full': ROOT / 'data/《中国药典》2025年版 一部 全本.pdf'}
    expected = {b['source']['source_document']: b['source']['source_sha256'] for b in blocks.values()}
    for label, sha in expected.items():
        if digest(paths[label]) != sha:
            raise ValueError('Original PDF changed')
        docs[label] = fitz.open(paths[label])
    output.mkdir(parents=True)
    (output / 'assets').mkdir()
    selection = {'kind': 'mineru_review_selection', 'seed': SEED,
                 'entries_sha256': summary['entries_sha256'], 'blocks_sha256': summary['blocks_sha256'],
                 'flagged_entry_ids': [e['id'] for e in flagged],
                 'control_entry_ids': [e['id'] for e in controls],
                 'selection_rule': 'all flagged + two unflagged per starting part ranked by sha256(seed:id) + all unflagged cross-part entries',
                 'reference_role': 'posthoc_ai_review_not_independent_gold', 'human_signoff': False}
    # Freeze IDs before rendering or inspecting source images.
    write(output / 'selection.json', selection)
    cases = []
    panels = []
    def crop(b, box, filename):
        source = b['source']
        page = docs[source['source_document']][source['source_pdf_page'] - 1]
        rect = fitz.Rect(box[0] / 1000 * page.rect.width, box[1] / 1000 * page.rect.height,
                         box[2] / 1000 * page.rect.width, box[3] / 1000 * page.rect.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(780 / rect.width, 780 / rect.width), clip=rect, alpha=False)
        target = output / 'assets' / filename
        pix.save(target)
        return {'image': f'assets/{filename}', 'sha256': digest(target),
                'source_document': source['source_document'], 'source_sha256': source['source_sha256'],
                'source_pdf_page': source['source_pdf_page'], 'chapter_page': b['chapter_page'],
                'bbox_0_1000': box}
    for e in flagged + controls:
        evidence = [blocks[k] for k in e['title_block_ids']]
        first = evidence[0]
        same = [b for b in evidence if b['chapter_page'] == first['chapter_page'] and b['column'] == first['column']]
        origins = [f for f in e['fields'] if f['scope'] == 'monograph' and f['label'] == 'origin']
        if origins:
            origin = blocks[origins[0]['segments'][0]['block_id']]
            if (origin['chapter_page'], origin['column']) == (first['chapter_page'], first['column']):
                same.append(origin)
        x0, x1 = (55, 495) if first['column'] == 'left' else (495, 945)
        y0 = max(0, min(b['raw']['bbox'][1] for b in same) - 16)
        y1 = min(995, max(b['raw']['bbox'][3] for b in same) + 30)
        # Include the gap where omitted pinyin/Latin lines might exist.
        y1 = min(995, max(y1, first['raw']['bbox'][3] + 110))
        previews = [crop(first, [x0, y0, x1, y1], f"{e['id']}-title.png")]
        clinical = [f for f in e['fields'] if f['label'] in ('性味', '性味与归经', '功能与主治', '用法与用量', '注意')]
        selected_ids = {s['block_id'] for f in clinical for s in f['segments']}
        # Include captions/formulas and flagged wide blocks for risk review.
        if e['flags']:
            selected_ids.update(k for k in e['block_ids'] if blocks[k]['raw']['type'] in ('chart', 'equation', 'image') or blocks[k]['flags'])
        for n, k in enumerate(sorted(selected_ids, key=e['block_ids'].index)):
            b = blocks[k]; box = b['raw']['bbox']
            previews.append(crop(b, [max(0, box[0] - 15), max(0, box[1] - 12),
                                     min(1000, box[2] + 15), min(1000, box[3] + 12)],
                                 f"{e['id']}-field-{n:02}.png"))
        # For cross-part entries include both sides of the upload boundary.
        joins = []
        eb = [blocks[k] for k in e['block_ids']]
        for a, b in zip(eb, eb[1:]):
            if a['part'] != b['part']:
                pair = []
                for suffix, item in [('before', a), ('after', b)]:
                    box = item['raw']['bbox']
                    pair.append(crop(item, [max(0, box[0] - 15), max(0, box[1] - 12),
                                           min(1000, box[2] + 15), min(1000, box[3] + 12)],
                                     f"{e['id']}-join-{suffix}.png"))
                joins.append(pair)
        boundary_previews = []
        if not e['flags']:
            pos = entries.index(e)
            if pos + 1 < len(entries):
                last = blocks[e['block_ids'][-1]]
                following = blocks[entries[pos + 1]['title_block_ids'][0]]
                for suffix, item in [('end', last), ('next-title', following)]:
                    box = item['raw']['bbox']
                    x0, x1 = (55, 495) if item['column'] == 'left' else (495, 945)
                    boundary_previews.append(crop(item, [x0, max(0, box[1] - 15), x1,
                                                        min(995, box[3] + 75)],
                                                  f"{e['id']}-boundary-{suffix}.png"))
        case = {'entry_id': e['id'], 'name_zh_candidate': e['name_zh_candidate'],
                'group': 'flagged' if e['flags'] else 'control', 'flags': e['flags'],
                'chapter_pages': e['chapter_pages'], 'previews': previews, 'cross_part_previews': joins,
                'boundary_previews': boundary_previews,
                'raw_blocks': [{'block_id': k, 'chapter_page': blocks[k]['chapter_page'],
                                'raw': blocks[k]['raw']} for k in e['block_ids']],
                'fields': e['fields'], 'ai_review': 'not_yet_reviewed', 'human_signoff': False}
        cases.append(case)
        panel = Image.open(output / previews[0]['image']).convert('RGB')
        # Contact sheets show titles/sources only; clinical fields have separate crops.
        panel.thumbnail((780, 570))
        tile = Image.new('RGB', (800, 620), 'white')
        ImageDraw.Draw(tile).text((10, 8), f"{e['id']} | chapter {','.join(map(str, e['chapter_pages']))}", fill='black')
        tile.paste(panel, (10, 35));panels.append(tile)
    for n in range(0, len(panels), 6):
        sheet = Image.new('RGB', (1600, 1860), '#ddd')
        for offset, panel in enumerate(panels[n:n + 6]):
            sheet.paste(panel, ((offset % 2) * 800, (offset // 2) * 620))
        sheet.save(output / f'titles-{n // 6 + 1:02}.jpg', quality=94)
    write(output / 'cases.json', {'cases': cases})
    parts = ['<!doctype html><meta charset="utf-8"><title>MinerU原图复核</title>',
             '<style>body{max-width:1500px;margin:auto;font:16px sans-serif}img{max-width:100%}pre{white-space:pre-wrap}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}section{border-top:2px solid #999;margin:24px 0}</style>',
             '<h1>MinerU原图复核：AI待审，不代表人工批准</h1>']
    for case in cases:
        parts.append(f'<section id="{case["entry_id"]}"><h2>{html.escape(case["entry_id"] + " " + case["name_zh_candidate"])}</h2><p>{html.escape(str(case["flags"]))}</p>')
        for preview in (case['previews'] + [x for pair in case['cross_part_previews'] for x in pair]
                        + case['boundary_previews']):
            parts.append(f'<p>章节{preview["chapter_page"]} / 原{preview["source_document"]} PDF物理页{preview["source_pdf_page"]}</p><img loading="lazy" src="{preview["image"]}">')
        parts.append('<details><summary>识别原文（未经纠正）</summary><pre>' + html.escape('\n'.join(b['raw'].get('text', str(b['raw'])) for b in case['raw_blocks'])) + '</pre></details></section>')
    (output / 'index.html').write_text('\n'.join(parts), encoding='utf-8')
    for doc in docs.values():
        doc.close()
    report = {'flagged_cases': len(flagged), 'control_cases': len(controls), 'total_cases': len(cases),
              'preview_images': sum(len(c['previews']) + 2 * len(c['cross_part_previews'])
                                    + len(c['boundary_previews']) for c in cases),
              'selection_sha256': digest(output / 'selection.json'), 'cases_sha256': digest(output / 'cases.json'),
              'builder_sha256': digest(Path(__file__)), 'human_signoff': False, 'new_ocr_pages': 0}
    write(output / 'summary.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=ROOT / 'var/mineru_pharmacopoeia_v1_ready')
    parser.add_argument('--out', type=Path, default=ROOT / 'var/mineru_review_v1')
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.out), ensure_ascii=False))
