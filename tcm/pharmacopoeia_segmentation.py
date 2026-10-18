"""Conservative, evidence-preserving segmentation of unreviewed two-column OCR."""
import hashlib
import json
import math
import re
import unicodedata

from tcm.pharmacopoeia import extract_fields

VERSION = 5
SOURCE_OPENERS = ('本品为', '本品由', '本品以', '本品系')


def compact(text):
    return ''.join(unicodedata.normalize('NFKC', text).split())


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def page_lines(page):
    """Validate all geometry; retain original line indexes even when excluding margins."""
    width, height = page['pdf_width'], page['pdf_height']
    if not all(isinstance(v, (float, int)) and math.isfinite(v) and v > 0 for v in (width, height)):
        raise ValueError('Invalid page dimensions')
    kept, excluded = [], []
    for index, raw in enumerate(page['lines']):
        box, score = raw['pdf_box'], raw['score']
        if (not isinstance(raw['text'], str) or len(box) != 4 or
            any(len(p) != 2 or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in p)
                or not 0 <= p[0] <= width or not 0 <= p[1] <= height for p in box) or
            not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1):
            raise ValueError('Invalid OCR evidence')
        x, y = (sum(p[k] for p in box) / 4 for k in (0, 1))
        column = 'left' if x < width / 2 else 'right'
        line = {**raw, 'line_index': index, 'document': page['document'],
                'pdf_page': page['pdf_page'], 'printed_page': page.get('printed_page'),
                'source_sha256': page['source_sha256'], 'column': column,
                'center_x_fraction': x / width, 'center_y_fraction': y / height, 'page_result_sha256': page['page_result_sha256']}
        text = compact(raw['text'])
        reason = None
        if y / height < .08:
            reason = 'top_running_header_margin'
        elif re.fullmatch(r'[·•.]*\d{1,4}[·•.]*', text):
            decorated = any(mark in text for mark in '·•.')
            if ((decorated and y / height >= .94) or
                (not decorated and y / height >= .96 and (x / width < .2 or x / width > .8))):
                reason = 'bottom_page_number_candidate'
        (excluded if reason else kept).append({**line, **({'reason': reason} if reason else {})})
    kept.sort(key=lambda l: (l['column'] == 'right', min(p[1] for p in l['pdf_box'])))
    return kept, excluded


def title_at(lines, index):
    """Use aligned title evidence; degraded identities require an origin opener."""
    part = lines[index:index + 7]
    if len(part) < 3:
        return None
    name = compact(part[0]['text'])
    name_parts = re.fullmatch(r'([\u4e00-\u9fff]{1,12})(?:\(([\u4e00-\u9fff]{1,12})\))?', name)
    py_index = 1 if name_parts else 0
    pinyin = part[py_index]['text'].strip()
    formal = bool(re.fullmatch(r'[A-Za-z]{4,}', compact(pinyin)) and re.search('[a-z]', pinyin))
    damaged = bool(re.fullmatch(r"[A-Za-z0-9 !|.,'~:_-]{3,40}", pinyin) and len(re.findall('[a-z]', pinyin)) >= 3)
    if not formal and not damaged:
        return None
    end = py_index + 1
    latin_start = end
    while end < min(latin_start + 3, len(part)) and re.fullmatch(r'[A-Z]{5,}', compact(part[end]['text'])):
        end += 1
    if end == latin_start:
        return None
    if not name_parts or not formal:
        if end >= len(part):
            return None
        opener = part[end]
        if ((opener['document'], opener['pdf_page'], opener['column']) !=
            (part[0]['document'], part[0]['pdf_page'], part[0]['column']) or
            not compact(opener['text']).startswith(SOURCE_OPENERS) or
            not 0 < opener['center_y_fraction'] - part[end-1]['center_y_fraction'] <= .07):
            return None
    evidence = part[:end]
    if len({(l['document'], l['pdf_page'], l['column']) for l in evidence}) != 1:
        return None
    ys = [l['center_y_fraction'] for l in evidence]
    xs = [l['center_x_fraction'] for l in evidence]
    if any(not 0 < b - a <= .045 for a, b in zip(ys, ys[1:])) or max(xs) - min(xs) > .08:
        return None
    flags = []
    if not name_parts:
        flags.append('chinese_title_not_observed')
    elif len(name_parts.group(1)) < 2:
        flags.append('short_chinese_title_identity_unresolved')
    if not formal:
        flags.append('pinyin_pattern_unresolved')
    if any(l['score'] < .9 for l in evidence):
        flags.append('low_confidence_title_identity')
    return {'name_zh_candidate': name if name_parts else None,
            'pinyin_candidate': part[py_index]['text'],
            'latin_candidate': '\n'.join(l['text'] for l in part[latin_start:end]),
            'title_name_parts': {'primary_candidate': name_parts.group(1) if name_parts else None,
                                 'parenthetical_candidate': name_parts.group(2) if name_parts else None},
            'pinyin_status': 'unverified_ocr' if formal else 'unresolved_ocr',
            'pinyin_evidence': part[py_index:py_index+1], 'latin_evidence': part[latin_start:end],
            'evidence': evidence, 'title_flags': flags,
            'identity_status': 'unresolved' if flags else 'unverified_ocr',
            'method': 'aligned_title_evidence_with_unresolved_identity_v5'}


def sections(lines):
    groups = [{'scope': 'monograph', 'lines': []}]
    for line in lines:
        text = compact(line['text'])
        scope = ('prepared_slices' if text == '饮片' else 'appendix_standard' if
                 re.match(r'^(?:附\d*[.:：]|\d+[.、]).*质量标准', text) else None)
        if scope:
            groups.append({'scope': scope, 'lines': []})
        groups[-1]['lines'].append(line)
    for group in groups:
        fields, issues = extract_fields(group['lines'], source_openers=SOURCE_OPENERS)
        if group['scope'] != 'monograph':
            issues = [i for i in issues if i['code'] != 'origin_boundary_unresolved']
            # An auxiliary section is not an independent identity or source statement.
            for key in ('origin', 'medicinal_part', 'pinyin', 'latin_drug_name'):
                fields[key] = {'state': 'not_inferred_for_auxiliary_section', 'occurrences': []}
        group.update(fields=fields, issues=issues)
    return groups


def segment_pages(pages):
    """Never join different PDFs, physical gaps or known printed-page gaps."""
    entries, orphans, margins, current = [], [], [], None
    previous = None
    orphan = []
    def close(reason):
        nonlocal current, orphan
        if current:
            current['end_boundary'] = reason
            current['boundary_status'] = ('bounded_by_next_title' if reason == 'next_title'
                                          else 'open_requires_following_context')
            current['sections'] = sections(current['lines'])
            primary = current['sections'][0]['fields']
            # Identities have their own title evidence, even if the source opener
            # vanished from OCR. Never fabricate source text from a later line.
            for key, text, evidence in (
                ('pinyin', current['pinyin_candidate'], current['pinyin_evidence']),
                ('latin_drug_name', current['latin_candidate'], current['latin_evidence'])):
                primary[key] = {'state': 'candidate', 'occurrences': [
                    {'raw_text': text, 'evidence': evidence, 'end_boundary': 'title_triplet_end',
                     'human_review': 'pending'}]}
            if current['pinyin_status'] == 'unresolved_ocr':
                primary['pinyin']['state'] = 'unresolved_ocr_value'
                primary['pinyin']['message'] = '疑似拼音行存在识别损坏，保留原始文字，不能用于身份映射。'
            if primary['origin']['state'] != 'candidate':
                current['title_flags'].append('origin_opener_not_observed')
            current['source_status'] = ('unreviewed_source_candidate' if primary['origin']['state'] == 'candidate'
                                        else 'unresolved_missing_source_opener')
            if current['title_flags']:
                current['review_status'] = 'requires_source_review'
            entries.append(current)
            current = None
        if orphan:
            orphans.append({'reason': 'leading_continuation_without_title', 'lines': orphan})
            orphan = []
    for page in pages:
        if previous is not None:
            physical = (previous['document'], previous['source_sha256'], previous['pdf_page'] + 1)
            actual = (page['document'], page['source_sha256'], page['pdf_page'])
            known_printed_gap = (previous.get('printed_page') is not None and page.get('printed_page') is not None
                                 and page['printed_page'] != previous['printed_page'] + 1)
            if physical != actual or known_printed_gap:
                close('source_or_page_gap')
        lines, excluded = page_lines(page)
        margins.extend(excluded)
        for index, line in enumerate(lines):
            inside_title = current and any(
                (l['document'], l['pdf_page'], l['column'], l['line_index']) ==
                (line['document'], line['pdf_page'], line['column'], line['line_index'])
                for l in current['evidence'])
            title = None if inside_title else title_at(lines, index)
            if title:
                close('next_title')
                identity = {k: line[k] for k in ('source_sha256', 'pdf_page', 'column', 'line_index')}
                current = {'id': fingerprint(identity)[:24], **title, 'lines': [],
                           'human_signoff': False, 'review_status': 'unreviewed_machine_candidate',
                           'canonical_entity_mapping': None}
            (current['lines'] if current else orphan).append(line)
        previous = page
    close('batch_end')
    return {'schema_version': VERSION, 'kind': 'unreviewed_automatic_monograph_segmentation',
            'human_signoff': False, 'entries': entries, 'leading_continuations': orphans,
            'excluded_margin_lines': margins,
            'metrics': {'entries': len(entries), 'bounded_entries': sum(e['end_boundary'] == 'next_title' for e in entries),
                        'open_entries': sum(e['end_boundary'] != 'next_title' for e in entries),
                        'leading_continuation_groups': len(orphans)}}
