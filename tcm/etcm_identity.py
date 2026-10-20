"""Literal source comparison, never taxonomic synonym resolution or entity approval."""
import re
import unicodedata

VERSION = 'etcm-identity-fields-v1'
STATUS_LABELS = {
    'medicinal_part_difference': '药用部位不一致，保持来源分离',
    'fields_consistent_not_verified': '基原文字与部位一致，尚未人工确认',
    'needs_identity_review': '基原文字未完全对应，保持来源分离',
    'insufficient_evidence': '核对信息不足，保持来源分离',
}
PARTS = {'root': '根', 'rhizome': '根茎', 'leaf': '叶', 'flower': '花',
         'fruit': '果实', 'seed': '种子', 'bark': '树皮', 'whole plant': '全草'}


def normalize_latin(text):
    # Keep authors, infraspecific ranks and punctuation: do not collapse species into varieties.
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text)).casefold()


def latin_names(text):
    """Isolate complete Latin runs separated by Chinese text or semicolons."""
    runs = re.findall(r'[A-Za-z][A-Za-z0-9\s.(),×-]*', text)
    return sorted({run.strip(' ,') for run in runs
                   if re.match(r'^[A-Z][a-z]+\s+[a-z][a-z-]+(?:\s|\(|$)', run)})


def etcm_parts(value):
    tokens = [x.strip().casefold() for x in value.split(',')]
    if not tokens or any(x not in PARTS for x in tokens):
        return None
    return sorted({PARTS[x] for x in tokens})


def origin_parts(text):
    # Only the definition before its first full stop; avoid roots mentioned in harvesting instructions.
    definition = text.split('。', 1)[0]
    match = re.search(r'干燥([^。；;]{1,32})$', definition)
    if not match:
        return None
    tokens = re.split(r'和|及|、', match.group(1).strip())
    if any(x not in PARTS.values() for x in tokens):
        return None
    return sorted(set(tokens))


def compare(record, candidate, entry, blocks):
    """Compare original fields and OCR origin segments, retaining every source witness."""
    witnesses = []
    complete = not entry.get('title_flags')
    fields = [f for f in entry.get('fields', [])
              if f.get('scope') == 'monograph' and f.get('label') == 'origin']
    complete = complete and len(fields) == 1
    for field in fields:
        for segment in field.get('segments', []):
            block = blocks.get(segment.get('block_id'))
            if block is None or 'start' not in segment or 'end' not in segment:
                complete = False
                continue
            text = block.get('text', block.get('raw', {}).get('text', ''))
            start, end = segment['start'], segment['end']
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
                complete = False
                continue
            witnesses.append({'block_id': block['id'], 'start': start, 'end': end,
                              'text': text[start:end], 'source': block.get('source'),
                              'bbox': block.get('raw', {}).get('bbox')})
    text = ''.join(w['text'] for w in witnesses)
    source_fields = record['fields']
    latin = source_fields.get('latin_name', {})
    part = source_fields.get('medicinal_part', {})
    latin_value = latin.get('value') if latin.get('state') == 'present' else None
    part_value = part.get('value') if part.get('state') == 'present' else None
    left_names = latin_names(latin_value) if isinstance(latin_value, str) else []
    right_names = latin_names(text)
    left = {normalize_latin(x) for x in left_names}
    right = {normalize_latin(x) for x in right_names}
    latin_status = ('exact_normalized_match' if left and left == right else
                    'literal_difference' if left and right else 'unknown')
    left_parts = etcm_parts(part_value) if isinstance(part_value, str) else None
    right_parts = origin_parts(text)
    part_status = ('same' if left_parts and left_parts == right_parts else
                   'different' if left_parts and right_parts else 'unknown')
    if not complete or not witnesses or latin_status == 'unknown' or part_status == 'unknown':
        status = 'insufficient_evidence'
    elif part_status == 'different':
        status = 'medicinal_part_difference'
    elif latin_status == 'exact_normalized_match':
        status = 'fields_consistent_not_verified'
    else:
        status = 'needs_identity_review'
    return {'method': VERSION, 'status': status, 'label': STATUS_LABELS[status],
            'identity_verified': False, 'human_reviewed': False,
            'source_record_id': record['source_record_id'], 'entry_id': entry['id'],
            'name_zh': candidate['name_zh'], 'etcm_source': record['source'],
            'title_evidence': candidate,
            'latin_comparison': {'status': latin_status, 'etcm_original': latin_value,
                                 'etcm_names': left_names, 'pharmacopoeia_names': right_names,
                                 'etcm_field_source': latin.get('source')},
            'part_comparison': {'status': part_status, 'etcm_original': part_value,
                                'etcm_parts': left_parts, 'pharmacopoeia_parts': right_parts,
                                'etcm_field_source': part.get('source')},
            'origin_evidence': witnesses,
            'scope': 'literal_fields_only_not_taxonomic_or_preparation_equivalence'}
