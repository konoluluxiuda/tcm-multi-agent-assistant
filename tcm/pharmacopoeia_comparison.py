"""Unreviewed field comparison, independent of OCR frameworks and expected answers."""
import math
import re
from difflib import SequenceMatcher
import copy
import unicodedata

from tcm.pharmacopoeia import extract_fields, MISSING, MISSING_MESSAGE

ENGINES = ('rapidocr', 'paddle')


def normalized(text):
    return ''.join(unicodedata.normalize('NFKC', text).split())


def compare_field(left, right):
    a = [o['raw_text'] for o in left['occurrences']]
    b = [o['raw_text'] for o in right['occurrences']]
    if not a and not b:
        return 'both_unavailable'
    if not a or not b:
        return 'presence_difference'
    if len(a) != len(b):
        return 'occurrence_count_difference'
    if a == b:
        return 'exact_agreement'
    if list(map(normalized, a)) == list(map(normalized, b)):
        return 'normalized_agreement'
    return 'text_difference'


def field_diff(left, right):
    a, b = ('\n\n'.join(o['raw_text'] for o in value['occurrences']) for value in (left, right))
    if len(a)+len(b) > 20000:
        return {'rapidocr': [{'text': a, 'changed': False}], 'paddle': [{'text': b, 'changed': False}],
                'highlight_status': 'skipped_size_limit'}
    result = {'rapidocr': [], 'paddle': [], 'highlight_status': 'computed'}
    for kind, i, j, k, l in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if i != j:
            result['rapidocr'].append({'text': a[i:j], 'changed': kind != 'equal'})
        if k != l:
            result['paddle'].append({'text': b[k:l], 'changed': kind != 'equal'})
    return result


def validate_page(page, expected):
    for key in ('source_sha256', 'image_sha256', 'pdf_page', 'document', 'pdf_width', 'pdf_height'):
        if page[key] != expected[key]:
            raise ValueError('Page metadata mismatch: '+key)
    width, height = page['pdf_width'], page['pdf_height']
    if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in (width, height)):
        raise ValueError('Invalid page dimensions')
    for line in page['lines']:
        box, score = line['pdf_box'], line['score']
        if not isinstance(line['text'], str) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError('Invalid OCR text/score')
        if len(box) != 4 or any(len(point) != 2 for point in box):
            raise ValueError('Invalid OCR box')
        for x, y in box:
            if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (x, y)) or not (0 <= x <= width and 0 <= y <= height):
                raise ValueError('OCR box outside page')


def select_lines(case, pages, documents, excluded=None):
    """Preserve manual region order; row sorting is not parser order evaluation."""
    selected = []
    for segment in case['segments']:
        label, number, column = segment['document'], segment['pdf_page'], segment['column']
        page = pages[f'{label}-{number}']
        if page['source_sha256'] != documents[label]['sha256']:
            raise ValueError('Wrong PDF source')
        low, high = segment['y_range']
        if column not in ('left', 'right') or not 0 <= low < high <= 1:
            raise ValueError('Invalid manual region')
        subset = []
        for index, line in enumerate(page['lines']):
            box = line['pdf_box']
            x = sum(p[0] for p in box)/len(box)/page['pdf_width']
            y = sum(p[1] for p in box)/len(box)/page['pdf_height']
            if ('left' if x < .5 else 'right') == column and low <= y <= high:
                printed_page = documents[label]['printed_pages'][str(number)]
                evidence = {**line, 'document': label, 'pdf_page': number, 'column': column,
                               'printed_page': printed_page,
                               'source_key': f'{label}-{number}-{column}', 'line_index': index,
                               'source_sha256': page['source_sha256'], 'image_sha256': page['image_sha256'],
                               'result_sha256': page['result_sha256']}
                # Only the exact, decorated known page number in the bottom margin.
                # Bare numbers, dosages and body text remain untouched.
                if printed_page is not None and y >= .94 and re.fullmatch(
                        r'[·•.]\s*'+re.escape(str(printed_page))+r'\s*[·•.]', line['text'].strip()):
                    if excluded is not None:
                        excluded.append({'reason': 'decorated_printed_page_in_bottom_margin', 'evidence': evidence})
                    continue
                subset.append(evidence)
        selected.extend(sorted(subset, key=lambda line: min(y for _, y in line['pdf_box'])))
    return selected


def build_comparison(suite, engines):
    records = []
    for case in suite['cases']:
        alternatives, issues, excluded = {}, {}, {}
        for engine in ENGINES:
            excluded[engine] = []
            lines = select_lines(case, engines[engine], suite['documents'], excluded[engine])
            alternatives[engine], issues[engine] = extract_fields(lines)
        fields = []
        for key in alternatives['rapidocr']:
            left, right = (alternatives[e][key] for e in ENGINES)
            fields.append({'key': key, 'comparison': compare_field(left, right),
                           'diff': field_diff(left, right),
                           'engines': {e: alternatives[e][key] for e in ENGINES},
                           'human_review': 'pending', 'reference_text': None,
                           'reviewer': None, 'reviewed_at': None, 'previews': []})
        records.append({'id': case['id'], 'name_zh_candidate': case['name_zh_candidate'],
                        'etcm_source_record_id': case['etcm_source_record_id'],
                        'mapping_status': case['mapping_status'], 'human_signoff': False,
                        'review_status': 'pending_human_review', 'fields': fields,
                        'extraction_issues': issues, 'excluded_margin_lines': excluded,
                        'regions': case['segments']})
    return {'schema_version': 1, 'kind': 'unreviewed_dual_engine_fields',
            'human_signoff': False, 'source_pdf_authenticity_verified': False,
            'missing_message': MISSING_MESSAGE, 'documents': suite['documents'],
            'comparison_normalization': 'NFKC + remove whitespace only; preserve case, punctuation, negation, digits.',
            'records': records}


def review_template(data):
    """Blank reference slots; OCR alternatives never become reference transcriptions."""
    return {'schema_version': 1, 'kind': 'blank_human_reference_template', 'human_signoff': False,
            'comparison_sha256': data['artifact_sha256'],
            'fields': [{'case_id': record['id'], 'field': field['key'],
                        'review_status': 'pending', 'reference_text': None, 'reviewer': None,
                        'reviewed_at': None, 'notes': None,
                        'sources': [{'engine': engine, 'evidence': occurrence['evidence']}
                                    for engine in ENGINES for occurrence in field['engines'][engine]['occurrences']]}
                       for record in data['records'] for field in record['fields']]}


def attach_visual_drafts(data, proposal, artifact_sha256, preview_hashes):
    """Attach explicitly AI-authored hypotheses; never populate human references."""
    if proposal.get('base_comparison_sha256') != artifact_sha256:
        raise ValueError('Visual draft uses a stale comparison artifact')
    if proposal.get('actor_type') != 'ai' or proposal.get('human_signoff') is not False:
        raise ValueError('AI visual drafts cannot assert human signoff')
    result = copy.deepcopy(data)
    fields = {(r['id'], f['key']): f for r in result['records'] for f in r['fields']}
    if data.get('human_signoff') is not False or any(
            f['reference_text'] is not None or f['human_review'] != 'pending' or
            f['reviewer'] is not None or f['reviewed_at'] is not None for f in fields.values()):
        raise ValueError('Cannot overwrite human reference/review data')
    scope = proposal.get('scope', 'disagreements')
    if scope not in ('disagreements', 'all_available_fields'):
        raise ValueError('Unsupported visual draft scope')
    targets = {key for key, field in fields.items() if
               (field['comparison'] == 'text_difference' if scope == 'disagreements' else
                any(value['occurrences'] for value in field['engines'].values()))}
    seen = set()
    for draft in proposal['drafts']:
        key = (draft['case_id'], draft['field'])
        if key not in targets or key in seen:
            raise ValueError('Unexpected or duplicate visual draft target')
        seen.add(key)
        field = fields[key]
        if field['reference_text'] is not None or field['human_review'] != 'pending':
            raise ValueError('Cannot overwrite a human reference or review state')
        if not isinstance(draft['draft_text'], str) or not draft['draft_text'].strip():
            raise ValueError('Visual transcription is empty')
        if not isinstance(draft['notes'], list) or not all(isinstance(note, str) for note in draft['notes']):
            raise ValueError('Visual draft notes must be a list of strings')
        expected = {(p['image'], p['source_sha256']) for p in field['previews']}
        supplied = {(p['image'], p['source_sha256']) for p in draft['sources']}
        if not expected or expected != supplied or len(supplied) != len(draft['sources']):
            raise ValueError('Visual draft source set mismatch')
        for source in draft['sources']:
            if preview_hashes.get(source['image']) != source['image_sha256']:
                raise ValueError('Visual draft image fingerprint mismatch')
        field['ai_visual_draft'] = {'status': 'ai_visual_draft_pending_human_review',
            'actor_type': 'ai', 'human_signoff': False, 'draft_text': draft['draft_text'],
            'notes': draft['notes'], 'sources': copy.deepcopy(draft['sources'])}
    if seen != targets:
        raise ValueError('Visual draft coverage does not match disagreement fields')
    observations = proposal.get('missing_observations', [])
    missing_targets = {key for key, field in fields.items() if
                       all(value['state'] == MISSING for value in field['engines'].values())}
    observed = set()
    for note in observations:
        key = (note['case_id'], note['field'])
        if scope != 'all_available_fields' or key not in missing_targets or key in observed:
            raise ValueError('Unexpected or duplicate missing-field observation')
        if note.get('finding') != 'not_observed_in_selected_region' or 'draft_text' in note:
            raise ValueError('Missing observation cannot assert absent contraindications or provide a transcription')
        record = next(r for r in result['records'] if r['id'] == key[0])
        expected = {(p['image'], p['source_sha256']) for p in record['region_previews']}
        supplied = {(p['image'], p['source_sha256']) for p in note['sources']}
        if not expected or supplied != expected or len(supplied) != len(note['sources']):
            raise ValueError('Missing observation must cover all selected region images')
        if any(preview_hashes.get(p['image']) != p['image_sha256'] for p in note['sources']):
            raise ValueError('Missing observation image fingerprint mismatch')
        observed.add(key)
        fields[key]['ai_visual_observation'] = {'actor_type': 'ai', 'human_signoff': False,
            'status': 'ai_visual_observation_pending_human_review',
            'finding': 'not_observed_in_selected_region', 'message': MISSING_MESSAGE,
            'sources': copy.deepcopy(note['sources'])}
    if scope == 'all_available_fields' and observed != missing_targets:
        raise ValueError('Missing-field observation coverage is incomplete')
    result['visual_draft_provenance'] = {'base_comparison_sha256': artifact_sha256,
        'actor_type': 'ai', 'scope': scope, 'human_signoff': False, 'draft_fields': len(seen),
        'missing_observations': len(observed),
        'human_reference_fields_completed': 0, 'approved_fields': 0}
    return result
