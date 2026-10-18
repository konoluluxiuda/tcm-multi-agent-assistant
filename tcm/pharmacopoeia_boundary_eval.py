"""Boundary-only comparison against pre-OCR visual references, never clinical approval."""
from collections import Counter

from tcm.pharmacopoeia_segmentation import compact


def evaluate_boundaries(reference, data):
    if reference.get('human_signoff') is not False or not (
        reference.get('frozen_before_ocr') or reference.get('frozen_before_result_inspection')):
        raise ValueError('This evaluator requires an explicitly unapproved reference frozen before output inspection')
    refs, pages = [], {}
    for page in reference['pages']:
        key = (page['document'], page['pdf_page'])
        if key in pages:
            raise ValueError('Duplicate reference page')
        pages[key] = page
        for index, title in enumerate(page['titles']):
            low, high = title['y_range']
            if title['column'] not in ('left', 'right') or not 0 <= low < high <= 1:
                raise ValueError('Invalid reference location')
            refs.append({**title, 'document': key[0], 'pdf_page': key[1], 'block': page['block'],
                         'id': f'{key[0]}-{key[1]}-{index}'})
    predictions = []
    for entry in data['entries']:
        line = entry['evidence'][0]
        key = (line['document'], line['pdf_page'])
        if key not in pages:
            raise ValueError('Prediction outside evaluated pages')
        predictions.append({'id': entry['id'], 'name': entry['name_zh_candidate'],
                            **{k: line[k] for k in ('document', 'pdf_page', 'column', 'center_y_fraction')}})
    matches = [[j for j, pred in enumerate(predictions)
                if (ref['document'], ref['pdf_page'], ref['column']) ==
                   (pred['document'], pred['pdf_page'], pred['column']) and
                ref['y_range'][0] <= pred['center_y_fraction'] <= ref['y_range'][1]] for ref in refs]
    degrees = Counter(j for candidates in matches for j in candidates)
    paired, missed, ambiguous, risks = [], [], [], set()
    for ref, candidates in zip(refs, matches):
        if len(candidates) == 1 and degrees[candidates[0]] == 1:
            prediction = predictions[candidates[0]]
            paired.append({'reference': ref, 'prediction': prediction,
                           'name_matches': compact(ref['name']) == compact(prediction['name'] or '')})
        elif candidates:
            ambiguous.append({'reference': ref, 'prediction_ids': [predictions[j]['id'] for j in candidates]})
        else:
            owners, leading = [], False
            # This is an exposure test, not a new segmentation heuristic. A missed
            # visible title retained inside an earlier entry demonstrates mixing.
            for entry in data['entries']:
                matching = [line for line in entry['lines'] if
                            (line['document'], line['pdf_page'], line['column']) ==
                            (ref['document'], ref['pdf_page'], ref['column']) and
                            ref['y_range'][0] <= line['center_y_fraction'] <= ref['y_range'][1] and
                            compact(line['text']) == compact(ref['name'])]
                if matching:
                    owners.append({'entry_id': entry['id'], 'entry_name': entry['name_zh_candidate'],
                                   'retained_title_line_indexes': [line['line_index'] for line in matching]})
                    risks.add(entry['id'])
            for group in data.get('leading_continuations', []):
                if any((line['document'], line['pdf_page'], line['column']) ==
                       (ref['document'], ref['pdf_page'], ref['column']) and
                       compact(line['text']) == compact(ref['name']) and
                       ref['y_range'][0] <= line['center_y_fraction'] <= ref['y_range'][1]
                       for line in group['lines']):
                    leading = True
            missed.append({'reference': ref, 'containing_entries': owners,
                           'found_in_leading_continuation': leading,
                           'impact': 'cross_entry_mixing' if owners else 'unresolved_missing_start'})
    unexpected = [pred for j, pred in enumerate(predictions) if not degrees[j]]
    block_violations = []
    for entry in data['entries']:
        blocks = {pages[(l['document'], l['pdf_page'])]['block'] for l in entry['lines']}
        if len(blocks) > 1:
            block_violations.append(entry['id'])
    failures = bool(missed or ambiguous or unexpected or block_violations)
    return {'kind': 'frozen_rule_boundary_evaluation',
            'status': 'completed_with_boundary_failures' if failures else 'completed_without_observed_boundary_failures',
            'human_signoff': False, 'reference_authority': 'AI_visual_pending_human_review',
            'scope': 'Observed title-start correspondence on purposive pages; not full-text accuracy or a generalization estimate.',
            'counts': {'pages': len(pages), 'reference_starts': len(refs), 'detected_starts': len(predictions),
                       'location_matches': len(paired), 'missed_starts': len(missed),
                       'unexpected_starts': len(unexpected), 'ambiguous_reference_starts': len(ambiguous),
                       'name_differences_among_matches': sum(not pair['name_matches'] for pair in paired),
                       'entries_exposed_to_missed_title': len(risks), 'cross_block_entries': len(block_violations),
                       'cross_page_entries': sum(len({(l['document'], l['pdf_page']) for l in e['lines']}) > 1
                                                 for e in data['entries']),
                       'open_entries': sum(e['end_boundary'] != 'next_title' for e in data['entries'])},
            'matched_starts': paired, 'missed_starts': missed, 'unexpected_starts': unexpected,
            'ambiguous_starts': ambiguous, 'cross_block_entry_ids': block_violations,
            'quarantine_entry_ids': sorted(risks | set(block_violations)),
            'ocr_character_accuracy': None, 'clinical_accuracy': None, 'field_scope_accuracy': None,
            'observed_source_page_gaps': [{'document': a['document'], 'before_pdf_page': a['pdf_page'],
                                         'after_pdf_page': b['pdf_page'], 'before_printed_page': a['printed_page'],
                                         'after_printed_page': b['printed_page']}
                for a, b in zip(reference['pages'], reference['pages'][1:])
                if a['document'] == b['document'] and b['pdf_page'] == a['pdf_page'] + 1 and
                a.get('printed_page') is not None and b.get('printed_page') is not None and
                b['printed_page'] != a['printed_page'] + 1]}
