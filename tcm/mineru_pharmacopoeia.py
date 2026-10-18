"""Evidence-preserving adapter for the exported two-column herb chapter.

Outputs are unreviewed research candidates, never production knowledge records.
All offsets refer to the unchanged MinerU text in blocks.json.
"""
import difflib
import hashlib
import json
import math
import re
import unicodedata
from collections import Counter

VERSION = 4
MARGINS = {'header', 'footer', 'page_number'}
OPENERS = ('本品为', '本品由', '本品以', '本品系')
NAME = re.compile(r'^([\u4e00-\u9fff]{1,16}(?:\([\u4e00-\u9fff、]{1,20}\))?)(.*)$', re.S)
LABEL = re.compile(r'【([^】\n]{1,24})】')


def compact(value):
    return ''.join(unicodedata.normalize('NFKC', value).split())


def digest(path):
    result = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def normalize_blocks(raw, part, page_rows, input_sha):
    """Keep original indices; explicitly repair order by two-column geometry."""
    if not isinstance(raw, list):
        raise ValueError('content_list must be a block array')
    result = []
    for index, block in enumerate(raw):
        page = block.get('page_idx')
        bbox = block.get('bbox')
        if (type(page) is not int or page not in page_rows or not isinstance(bbox, list)
                or len(bbox) != 4 or any(type(v) not in (int, float) or not math.isfinite(v)
                                         or not 0 <= v <= 1000 for v in bbox)
                or bbox[0] > bbox[2] or bbox[1] > bbox[3]
                or not isinstance(block.get('type'), str)
                or ('text' in block and not isinstance(block['text'], str))):
            raise ValueError(f'Invalid MinerU block {part}:{index}')
        row = page_rows[page]
        flags = []
        if bbox[0] < 450 and bbox[2] > 550 and block['type'] not in MARGINS:
            flags.append('spans_columns_requires_review')
        result.append({'id': f'{part}:{index}', 'raw': block, 'part': part,
                       'input_sha256': input_sha, 'raw_block_index': index,
                       'chapter_page': row['chapter_pdf_page'], 'source': row,
                       'column': 'left' if bbox[0] + bbox[2] < 1000 else 'right',
                       'flags': flags})
    expected = set(page_rows)
    if {b['raw']['page_idx'] for b in result} != expected:
        raise ValueError(f'Incomplete page coverage in {part}')
    result.sort(key=lambda b: (b['chapter_page'], b['column'] == 'right',
                               b['raw']['bbox'][1], b['raw_block_index']))
    return order_wide_tables(result)


def order_wide_tables(blocks):
    """A full-width table separates the two-column flow above and below it.

    Ambiguous overlap remains flagged rather than silently assigning a side.
    This only handles tables; other wide objects retain the review requirement.
    """
    ordered = []
    for page in sorted({b['chapter_page'] for b in blocks}):
        items = [b for b in blocks if b['chapter_page'] == page]
        wide = sorted([b for b in items if b['raw']['type'] == 'table'
                       and 'spans_columns_requires_review' in b['flags']],
                      key=lambda b: b['raw']['bbox'][1])
        body = [b for b in items if b['raw']['type'] not in MARGINS and b not in wide]
        overlap = any(a['raw']['bbox'][3] > b['raw']['bbox'][1]
                      for a, b in zip(wide, wide[1:]))
        overlap |= any(b['raw']['bbox'][1] < table['raw']['bbox'][3]
                       and b['raw']['bbox'][3] > table['raw']['bbox'][1]
                       for b in body for table in wide)
        if not wide or overlap:
            if wide and overlap:
                for b in items:
                    if b['raw']['type'] not in MARGINS:
                        b['flags'].append('wide_table_overlap_order_unresolved')
            ordered.extend(items)
            continue
        remaining = list(items)
        for table in wide:
            before = [b for b in remaining if b is not table
                      and b['raw']['bbox'][3] <= table['raw']['bbox'][1]]
            ordered.extend(before)  # Existing left/right order is retained within each band.
            ordered.append(table)
            removed = {b['id'] for b in before} | {table['id']}
            remaining = [b for b in remaining if b['id'] not in removed]
            table['reading_order_policy'] = 'full_width_table_separates_column_bands'
        ordered.extend(remaining)
    return ordered


def auxiliary_heading(text):
    value = compact(text)
    return value == '饮片' or bool(re.match(r'^(?:附[:：]?\d*[.、]?|\d+[.、]).*质量标准', value))


def attach_caption_evidence(blocks, layout, layout_sha):
    """Join immutable caption strings to their individual layout coordinates."""
    for block in blocks:
        raw = block['raw']
        kind = raw['type']
        if kind not in ('chart', 'table'):
            continue
        keys = [f'{kind}_caption', f'{kind}_footnote']
        if not any(raw.get(key) for key in keys):
            continue
        page = layout['pdf_info'][raw['page_idx']]
        size = page['page_size']
        if len(size) != 2 or any(not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in size):
            raise ValueError('Invalid caption page geometry')
        filename = raw['img_path'].rsplit('/', 1)[-1]
        parents = [item for item in page['para_blocks'] if item['type'] == kind and any(
            span.get('image_path') == filename
            for child in item.get('blocks', []) for line in child.get('lines', [])
            for span in line.get('spans', []))]
        if len(parents) != 1:
            raise ValueError('Caption layout parent is not unique')
        evidence = []
        for key in keys:
            children = [item for item in parents[0]['blocks'] if item['type'] == key]
            strings = raw.get(key, [])
            if not isinstance(strings, list) or len(strings) != len(children):
                raise ValueError('Caption layout count mismatch')
            for index, (text, child) in enumerate(zip(strings, children)):
                observed = ''.join(span.get('content', '') for line in child.get('lines', [])
                                   for span in line.get('spans', []))
                if not isinstance(text, str) or compact(text.replace('$', '')) != compact(observed.replace('$', '')):
                    raise ValueError('Caption text does not match layout evidence')
                box = child['bbox']
                if (len(box) != 4 or any(not isinstance(v, (int, float)) or not math.isfinite(v)
                                         or not 0 <= v <= size[i % 2] for i, v in enumerate(box))
                        or box[0] >= box[2] or box[1] >= box[3]):
                    raise ValueError('Invalid caption geometry')
                evidence.append({'text_source': key, 'item_index': index,
                                 'bbox_0_1000': [v / size[i % 2] * 1000 for i, v in enumerate(box)],
                                 'layout_sha256': layout_sha, 'geometry_status': 'layout_child_not_asset_bbox'})
        block['caption_evidence'] = evidence
    return blocks


def segment_text(segment, blocks):
    """Read a text span from the unchanged original block or caption element."""
    raw = blocks[segment['block_id']]['raw']
    source = segment.get('text_source', 'text')
    if source == 'text':
        value = raw.get('text', '')
    elif source in ('chart_caption', 'chart_footnote', 'table_caption', 'table_footnote'):
        value = raw[source][segment['item_index']]
    else:
        raise ValueError('Unsupported text source')
    start, end = segment['start'], segment['end']
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(value):
        raise ValueError('Invalid source text offsets')
    return value[start:end]


def caption_stream(blocks):
    """Expand captions into geometry-ordered events without inventing raw blocks."""
    events = list(blocks)
    for block in blocks:
        for ref in block.get('caption_evidence', []):
            box = ref['bbox_0_1000']
            raw = block['raw']
            events.append({**block,
                           'id': f"{block['id']}#{ref['text_source']}:{ref['item_index']}",
                           'parent_id': block['id'], 'text_ref': {
                               'text_source': ref['text_source'], 'item_index': ref['item_index']},
                           'column': 'left' if box[0] + box[2] < 1000 else 'right', 'flags': [],
                           'raw': {'type': 'text', 'text': raw[ref['text_source']][ref['item_index']],
                                   'page_idx': raw['page_idx'], 'bbox': box}})
    events.sort(key=lambda b: (b['chapter_page'], b['column'] == 'right',
                               b['raw']['bbox'][1], b['raw_block_index']))
    return order_wide_tables(events)


def title_at(blocks, index, in_appendix=False):
    first = blocks[index]
    raw = first['raw']
    if raw['type'] != 'text' or 'parent_id' in first:
        return None
    value = compact(raw['text'])
    if auxiliary_heading(value):
        return None
    match = NAME.fullmatch(value)
    if not match or re.search(r'[\u4e00-\u9fff【】]', match[2]):
        return None
    group = []
    for block in blocks[index:index + 5]:
        item = block['raw']
        if ((block['chapter_page'], block['column']) != (first['chapter_page'], first['column'])
                or item['type'] != 'text'):
            break
        text = compact(item.get('text', ''))
        if group and (text.startswith(OPENERS) or LABEL.search(text)
                      or auxiliary_heading(text)):
            break
        if group and re.search(r'[\u4e00-\u9fff]', text):
            break
        group.append(block)
    tail = ' '.join(b['raw']['text'] for b in group)
    tail = re.sub(r'^[\u4e00-\u9fff\s（）()、]+', '', tail)
    latin = re.search(r'\b[A-Z]{2,}(?:[\s-]+[A-Z]{2,})*\b', tail)
    pinyin = bool(re.search(r'[a-zà-ž]', tail))
    inferred = not raw.get('text_level')
    if inferred:
        # Missing heading metadata needs all three identity cues plus a nearby
        # source opener. Caption events and auxiliary headings never qualify.
        if not (latin and pinyin):
            return None
        centers = [(b['raw']['bbox'][0] + b['raw']['bbox'][2]) / 2 for b in group]
        if max(centers) - min(centers) > 95:
            return None
        if any(not -3 <= b['raw']['bbox'][1] - a['raw']['bbox'][3] <= 70
               for a, b in zip(group, group[1:])):
            return None
        next_index = index + len(group)
        if next_index >= len(blocks):
            return None
        source = blocks[next_index]
        if 'parent_id' in source or not compact(source['raw'].get('text', '')).startswith(OPENERS):
            return None
        same_column = ((source['chapter_page'], source['column']) ==
                       (first['chapter_page'], first['column']))
        last_box, source_box = group[-1]['raw']['bbox'], source['raw']['bbox']
        nearby = same_column and 0 <= source_box[1] - last_box[3] <= 100
        reading_step = ((first['column'] == 'left' and source['column'] == 'right'
                         and source['chapter_page'] == first['chapter_page'])
                        or (first['column'] == 'right' and source['column'] == 'left'
                            and source['chapter_page'] == first['chapter_page'] + 1))
        continuation = reading_step and last_box[3] >= 850 and source_box[1] <= 200
        if not (nearby or continuation):
            return None
    # Even damaged identity evidence must stop contamination of the preceding herb.
    # Pure Chinese headings inside an appendix are components, not new monographs.
    if in_appendix and not (latin and pinyin):
        return None
    flags = []
    if inferred:
        flags.append('heading_level_missing_boundary_inferred')
    if not latin:
        flags.append('latin_title_not_observed')
    if not pinyin:
        flags.append('pinyin_title_not_observed')
    return {'name_zh_candidate': match[1], 'title_block_ids': [b['id'] for b in group],
            'title_flags': flags, 'method': ('aligned_title_and_source_without_heading_level'
                                           if inferred else 'chinese_heading_with_optional_latin_pinyin')}


def source_anchor(entry, blocks):
    """Stable within the frozen OCR import; independent of preceding entry count."""
    first = blocks[entry['title_block_ids'][0]]
    raw_hash = hashlib.sha256(json.dumps(first['raw'], sort_keys=True, ensure_ascii=False,
                                         separators=(',', ':')).encode()).hexdigest()
    return {'version': 1, 'source_sha256': first['source']['source_sha256'],
            'source_pdf_page': first['source']['source_pdf_page'],
            'bbox_0_1000': first['raw']['bbox'], 'raw_title_sha256': raw_hash}


def source_entry_id(entry, blocks):
    anchor = json.dumps(source_anchor(entry, blocks), sort_keys=True, ensure_ascii=False,
                        separators=(',', ':')).encode()
    return 'mineru-herb-' + hashlib.sha256(anchor).hexdigest()[:24]


def segment_blocks(all_blocks):
    blocks = [b for b in caption_stream(all_blocks) if b['raw']['type'] not in MARGINS]
    entries, orphan_ids, assigned, exclusions = [], [], [], []
    entry = None
    scope = 'monograph'
    field = None
    consumed_titles = set()
    in_appendix = False
    for index, block in enumerate(blocks):
        raw = block['raw']
        text = raw.get('text', '')
        value = compact(text)
        title = None if block['id'] in consumed_titles else title_at(blocks, index, in_appendix)
        if title:
            if entry:
                entry['end_boundary'] = 'next_title'
            entry = {'id': f"mineru-herb-{len(entries) + 1:04}", **title, 'block_ids': [],
                     'fields': [], 'flags': list(title['title_flags']),
                     'review_status': 'pending_human_review', 'production_eligible': False,
                     'end_boundary': 'chapter_end_unverified'}
            entries.append(entry)
            consumed_titles.update(title['title_block_ids'])
            scope, field, in_appendix = 'monograph', None, False
        if not entry:
            orphan_ids.append(block['id'])
            continue
        block_id = block.get('parent_id', block['id'])
        if block_id not in entry['block_ids']:
            entry['block_ids'].append(block_id)
            assigned.append(block_id)
        entry['flags'].extend(block['flags'])
        if block['id'] in consumed_titles:
            continue
        # Original blocks remain available, including suspected debris.
        if (raw['type'] == 'image' and raw['bbox'][1] >= 940):
            exclusions.append({'block_id': block['id'], 'reason': 'suspected_footer_image'})
            entry['flags'].append('suspected_footer_image_excluded_from_fields')
            continue
        if raw['type'] == 'text' and auxiliary_heading(value):
            if value == '饮片':
                scope = 'prepared_slices'
                in_appendix = False
            else:
                scope = f'appendix:{block["id"]}'
                in_appendix = True
            field = None
            continue
        if raw['type'] != 'text':
            if field is None:
                field = {'scope': scope, 'label': 'unclassified', 'segments': []}
                entry['fields'].append(field)
            field['segments'].append({'block_id': block['id']})
            if raw['type'] in ('chart', 'table'):
                captions = raw.get('chart_caption', []) + raw.get('table_caption', [])
                if any(LABEL.search(caption) for caption in captions):
                    entry['flags'].append('figure_caption_contains_field_heading')
            if raw['type'] == 'equation':
                entry['flags'].append('equation_requires_context_review')
            continue
        matches = list(LABEL.finditer(text))
        spans = [(0, matches[0].start() if matches else len(text), None)]
        spans += [(m.start(), matches[i + 1].start() if i + 1 < len(matches) else len(text),
                   compact(m[1])) for i, m in enumerate(matches)]
        for start, end, label in spans:
            if not text[start:end].strip():
                continue
            starts_origin = (value.startswith(OPENERS) and start == 0
                             and (field is None or field['label'] in ('origin', 'unclassified')))
            if label is not None or field is None or starts_origin:
                field = {'scope': scope, 'label': label or ('origin' if value.startswith(OPENERS)
                                                         else 'unclassified'), 'segments': []}
                entry['fields'].append(field)
            field['segments'].append({'block_id': block_id, **block.get('text_ref', {}),
                                      'start': start, 'end': end})
    by_id = {b['id']: b for b in all_blocks}
    for ordinal, item in enumerate(entries, 1):
        evidence = [by_id[k] for k in item['block_ids']]
        item['chapter_pages'] = sorted({b['chapter_page'] for b in evidence})
        item['parts'] = sorted({b['part'] for b in evidence})
        item['ordinal'] = ordinal
        item['source_anchor'] = source_anchor(item, by_id)
        item['id'] = source_entry_id(item, by_id)
        origins = [f for f in item['fields'] if f['scope'] == 'monograph' and f['label'] == 'origin']
        if not origins:
            item['flags'].append('origin_opener_not_observed')
        elif len(origins) > 1:
            item['flags'].append('multiple_origin_openers_requires_boundary_review')
        # Similar source names expose likely OCR disagreements, without renaming identities.
        for origin in origins[:1]:
            source_text = ''.join(segment_text(s, by_id)
                                  for s in origin['segments'] if 'start' in s)
            name = item['name_zh_candidate'].split('(')[0]
            for candidate in re.findall(r'(?:植物|动物)([\u4e00-\u9fff]{2,12})\s+[A-Z]', source_text):
                if (candidate != name and len(candidate) == len(name)
                        and difflib.SequenceMatcher(None, candidate, name).ratio() >= .6):
                    item['flags'].append('title_source_near_match_requires_review')
        if item['end_boundary'] != 'next_title':
            item['flags'].append('terminal_boundary_not_independently_verified')
        item['flags'] = sorted(set(item['flags']))
    if len({e['id'] for e in entries}) != len(entries):
        raise ValueError('Source entry identity collision')
    # Every non-margin block must have exactly one owner or an explicit orphan receipt.
    owned = assigned + orphan_ids
    if len(owned) != len(set(owned)) or set(owned) != {b['id'] for b in all_blocks if b['raw']['type'] not in MARGINS}:
        raise ValueError('Block ownership conservation failed')
    return {'entries': entries, 'orphan_block_ids': orphan_ids, 'field_exclusions': exclusions,
            'margin_block_ids': [b['id'] for b in all_blocks if b['raw']['type'] in MARGINS],
            'metrics': {'entries': len(entries), 'pages': len({b['chapter_page'] for b in all_blocks}),
                        'blocks': len(all_blocks), 'assigned_blocks': len(assigned),
                        'orphan_blocks': len(orphan_ids),
                        'margin_blocks': sum(b['raw']['type'] in MARGINS for b in all_blocks),
                        'cross_page_entries': sum(len(e['chapter_pages']) > 1 for e in entries),
                        'cross_part_entries': sum(len(e['parts']) > 1 for e in entries),
                        'flagged_entries': sum(bool(e['flags']) for e in entries),
                        'flag_counts': dict(Counter(f for e in entries for f in e['flags']))}}
