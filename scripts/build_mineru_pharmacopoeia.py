"""Build traceable, unreviewed herb candidates from the verified MinerU import."""
import argparse
import json
from pathlib import Path
import time

from tcm.mineru_pharmacopoeia import VERSION, attach_caption_evidence, digest, normalize_blocks, segment_blocks

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Input path escapes import directory')
    return path


def build(import_root, page_map, output):
    started = time.monotonic()
    if output.exists():
        raise FileExistsError('Use a fresh output directory; existing results are immutable')
    manifest_path = import_root / 'import_manifest.json'
    manifest_sha = digest(manifest_path)
    receipt = read(ROOT / 'audits/mineru_import/import_fingerprints.json')
    if manifest_sha != receipt['import_manifest_sha256']:
        raise ValueError('Import manifest changed since copy verification')
    # The page map is the frozen map used during PDF export verification.
    if digest(page_map) != digest(ROOT / 'audits/pharmacopoeia_herb_export/page_map.json'):
        raise ValueError('Page mapping differs from the verified export')
    manifest = read(manifest_path)
    rows = read(page_map)['preferred']
    if [r['chapter_pdf_page'] for r in rows] != list(range(1, len(rows) + 1)):
        raise ValueError('Page map must cover a contiguous chapter in order')
    fingerprints, all_blocks, seen_pages = {}, [], set()
    for batch in sorted(manifest['batches'], key=lambda b: b['chapter_first_page']):
        part = batch['part']
        for spec in batch['files']:
            path = inside(import_root, f"{part}/{spec['path']}")
            if path.stat().st_size != spec['bytes'] or digest(path) != spec['sha256']:
                raise ValueError(f'Imported file changed: {part}/{spec["path"]}')
            fingerprints[f"{part}/{spec['path']}"] = spec['sha256']
        specs = [s for s in batch['files'] if s['path'].endswith('_content_list.json')]
        if len(specs) != 1:
            raise ValueError('Expected exactly one v1 content_list per batch')
        spec = specs[0]
        first, last = batch['chapter_first_page'], batch['chapter_last_page']
        pages = {i: rows[first + i - 1] for i in range(last - first + 1)}
        if any(row['upload_part_page'] != i + 1 for i, row in pages.items()):
            raise ValueError('Upload page mapping mismatch')
        for row in pages.values():
            if row['chapter_pdf_page'] in seen_pages:
                raise ValueError('Overlapping batch pages')
            seen_pages.add(row['chapter_pdf_page'])
        normalized = normalize_blocks(read(inside(import_root, f"{part}/{spec['path']}")),
                                      part, pages, spec['sha256'])
        layout_spec = next(s for s in batch['files'] if s['path'] == 'layout.json')
        all_blocks.extend(attach_caption_evidence(normalized,
                          read(inside(import_root, f'{part}/layout.json')), layout_spec['sha256']))
    if seen_pages != {r['chapter_pdf_page'] for r in rows}:
        raise ValueError('Missing chapter pages')
    parsed = segment_blocks(all_blocks)
    image_path = import_root / 'image_index.json'
    if digest(image_path) != receipt['image_index_sha256']:
        raise ValueError('Image index changed since import')
    images = read(image_path)['images']
    for block in all_blocks:
        block['asset_paths'] = [im['path'] for im in images
                                if im['part'] == block['part']
                                and im['chapter_pdf_page'] == block['chapter_page']
                                and im['bbox_0_1000'] == block['raw']['bbox']]
    linked = {path for b in all_blocks for path in b['asset_paths']}
    if linked != {im['path'] for im in images}:
        raise ValueError('Some image/formula evidence was not linked to blocks')
    # Read-only source checks above finish before creating a new result directory.
    output.mkdir(parents=True, exist_ok=False)
    write(output / 'blocks.json', all_blocks)
    write(output / 'entries.json', parsed)
    summary = {'kind': 'mineru_pharmacopoeia_candidates', 'adapter_version': VERSION,
               'status': 'completed_pending_quality_review', 'human_signoff': False,
               'production_ingested': False, 'new_ocr_pages': 0,
               'import_manifest_sha256': manifest_sha, 'verified_import_files': len(fingerprints),
               'page_map_sha256': digest(page_map), 'metrics': parsed['metrics'],
               'images_linked': len(linked), 'field_exclusions': len(parsed['field_exclusions']),
               'caption_elements_recovered': sum(len(b.get('caption_evidence', [])) for b in all_blocks),
               'adapter_code_sha256': digest(ROOT / 'tcm/mineru_pharmacopoeia.py'),
               'builder_code_sha256': digest(Path(__file__)),
               'entries_sha256': digest(output / 'entries.json'),
               'blocks_sha256': digest(output / 'blocks.json'),
               'ordering_policy': 'chapter_page_then_wide_table_bands_then_left_right_columns_then_top_y',
               'elapsed_seconds': round(time.monotonic() - started, 3)}
    write(output / 'summary.json', summary)
    lines = ['# MinerU药材候选目录（未人审）', '', '| ID | 识别药名 | 章节页 | 待核实标记 |',
             '|---|---|---|---|']
    for item in parsed['entries']:
        lines.append(f"| {item['id']} | {item['name_zh_candidate']} | "
                     f"{','.join(map(str, item['chapter_pages']))} | {', '.join(item['flags'])} |")
    (output / 'catalog.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--import-root', type=Path, default=ROOT / 'var/mineru_import/2026-10-17')
    parser.add_argument('--page-map', type=Path, default=ROOT / 'var/pharmacopoeia_herbs_pdf/page_map.json')
    parser.add_argument('--out', type=Path, default=ROOT / 'var/mineru_pharmacopoeia_v4')
    args = parser.parse_args()
    print(json.dumps(build(args.import_root, args.page_map, args.out), ensure_ascii=False))


if __name__ == '__main__':
    main()
