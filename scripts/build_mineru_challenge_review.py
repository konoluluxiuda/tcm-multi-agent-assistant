"""Render local scan-anchor contact sheets without running challenge retrieval."""
import argparse
import json
from pathlib import Path

from tcm.mineru_pharmacopoeia import digest


def build(references, sources, output):
    import pymupdf
    from PIL import Image, ImageDraw, ImageOps
    if output.exists():
        raise FileExistsError('Use a fresh review directory')
    refs = json.loads(references.read_text(encoding='utf-8'))
    hashes = {name: digest(path) for name, path in sources.items()}
    seen, items = set(), []
    output.mkdir(parents=True)
    for ref in refs['references']:
        if ref['kind'] != 'corpus_proposed_evidence':
            continue
        groups = [('title', ref['title_evidence'][:1]),
                  ('field', [t['evidence'][0] for t in ref['targets']])]
        for kind, citations in groups:
            for citation in citations:
                key = json.dumps(citation, sort_keys=True)
                if key in seen:
                    continue
                seen.add(key)
                source = citation['source']; box = citation['bbox_0_1000']
                if hashes[source['source_document']] != source['source_sha256']:
                    raise ValueError('Source PDF differs from reference')
                with pymupdf.open(sources[source['source_document']]) as document:
                    page = document[source['source_pdf_page'] - 1]
                    region = [max(0, box[0] - 35), max(0, box[1] - 25),
                              min(1000, box[2] + 35), min(1000, box[3] + 35)]
                    rect = pymupdf.Rect(region[0] * page.rect.width / 1000, region[1] * page.rect.height / 1000,
                                        region[2] * page.rect.width / 1000, region[3] * page.rect.height / 1000)
                    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=rect, alpha=False)
                image = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
                file = output / f'anchor-{len(items) + 1:02}.png'; image.save(file)
                items.append({'case_id': ref['case_id'], 'kind': kind, 'citation': citation,
                              'image': file.name, 'image_sha256': digest(file)})
    for start in range(0, len(items), 12):
        sheet = Image.new('RGB', (1440, 1320), '#f5f5f0'); draw = ImageDraw.Draw(sheet)
        for i, item in enumerate(items[start:start + 12]):
            x, y = i % 2 * 720, i // 2 * 220
            source = item['citation']['source']
            draw.text((x + 12, y + 8), f"#{start + i + 1} {item['case_id']} {item['kind']} {source['source_document']} physical {source['source_pdf_page']}", fill='black')
            with Image.open(output / item['image']) as image:
                preview = ImageOps.contain(image, (696, 180))
                sheet.paste(preview, (x + 12, y + 32))
        sheet.save(output / f'contact-{start // 12 + 1}.png')
    (output / 'anchors.json').write_text(json.dumps({'reference_sha256': digest(references),
        'source_sha256': hashes, 'anchors': items}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return len(items)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--references', type=Path, default=Path('var/mineru_challenge_v1/reference.json'))
    parser.add_argument('--out', type=Path, default=Path('var/mineru_challenge_v1/review_rebuilt'))
    parser.add_argument('--high', type=Path, default=Path('data/《中国药典》2025年版 一部.pdf'))
    parser.add_argument('--full', type=Path, default=Path('data/《中国药典》2025年版 一部 全本.pdf'))
    args = parser.parse_args()
    print(json.dumps({'unique_anchors': build(args.references, {'high': args.high, 'full': args.full}, args.out)}))
