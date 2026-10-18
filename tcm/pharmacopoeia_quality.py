"""Machine quality gates and reproducible sampling, never human approval."""
from collections import Counter, defaultdict
import hashlib
import math
import re

from tcm.pharmacopoeia_comparison import compare_field, normalized

SAFETY = {'usage', 'cautions'}
IDENTITY = {'origin', 'pinyin', 'latin_drug_name', 'medicinal_part'}


def group(field):
    return 'safety' if field in SAFETY else 'identity' if field in IDENTITY else 'description'


def validate_policy(policy):
    if not 0 <= policy['minimum_line_confidence'] <= 1:
        raise ValueError('Invalid confidence threshold')
    if type(policy['sample_per_stratum']) is not int or not 0 <= policy['sample_per_stratum'] <= 100:
        raise ValueError('Invalid sample budget')
    if policy['allowed_use'] != 'unreviewed_research_source_excerpt_only':
        raise ValueError('Policy cannot promote research excerpts to clinical use')


def text(value):
    return '\n\n'.join(o['raw_text'] for o in value['occurrences'])


def terminal_period_only(a, b):
    a, b = normalized(a), normalized(b)
    return (a.endswith('。') and a[:-1] == b) or (b.endswith('。') and b[:-1] == a)


def evidence_errors(evidence, data, registry, engine):
    errors = set()
    for line in evidence:
        key = f"{line['document']}-{line['pdf_page']}"
        page = registry.get(key)
        doc = data['documents'].get(line['document'])
        if not page or not doc:
            errors.add('unknown_source_page'); continue
        if (line['source_sha256'] != doc['sha256'] or
                line['source_sha256'] != page['source_sha256'] or
                line['image_sha256'] != page['image_sha256'] or
                line['result_sha256'] != data['inputs'][engine]['page_results'].get(key) or
                line['printed_page'] != doc['printed_pages'].get(str(line['pdf_page']))):
            errors.add('source_fingerprint_mismatch')
        box = line['pdf_box']
        if len(box) != 4 or any(len(p) != 2 for p in box):
            errors.add('invalid_geometry'); continue
        if any(not isinstance(v, (int, float)) or not math.isfinite(v) for p in box for v in p):
            errors.add('invalid_geometry'); continue
        if any(not 0 <= x <= page['pdf_width'] or not 0 <= y <= page['pdf_height'] for x, y in box):
            errors.add('invalid_geometry')
    return errors


def evaluate(data, registry, policy):
    validate_policy(policy)
    fields = []
    for record in data['records']:
        for field in record['fields']:
            key = field['key']; reasons = set(); errors = set(); scores = []; evidence = []
            alternatives = field['engines']
            comparison = compare_field(alternatives['rapidocr'], alternatives['paddle'])
            available = any(v['occurrences'] for v in alternatives.values())
            for engine, value in alternatives.items():
                if value['state'] == 'unresolved_ocr_value': errors.add('unresolved_field')
                for occurrence in value['occurrences']:
                    if not occurrence['raw_text'].strip() or not occurrence['evidence']:
                        errors.add('empty_text_or_evidence')
                    if occurrence['end_boundary'] == 'selected_region_end':
                        reasons.add('manual_region_end')
                        if key != 'storage': errors.add('possibly_truncated_field')
                    evidence.extend(occurrence['evidence'])
                    errors.update(evidence_errors(occurrence['evidence'], data, registry, engine))
                    for line in occurrence['evidence']:
                        score = line['score']
                        if not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
                            errors.add('invalid_model_score')
                        else: scores.append(score)
                for issue in record['extraction_issues'][engine]:
                    if issue.get('blocking') and issue.get('field', key) == key:
                        errors.add('blocking_extraction_issue')
            source_ids = sorted({line['source_sha256'] for line in evidence})
            source_id = '|'.join(source_ids) or 'unknown'
            stratum = source_id+'|'+group(key)
            if any(s in policy['suspended_source_sha256'] for s in source_ids) or stratum in policy['suspended_strata']:
                errors.add('suspended_source_or_stratum')
            a, b = text(alternatives['rapidocr']), text(alternatives['paddle'])
            if any(re.search(r'[A-Za-z][\u0370-\u03ff\u0400-\u04ff][A-Za-z]', s) for s in (a, b)):
                reasons.add('mixed_script_in_latin_word')
            if errors:
                state, priority = 'blocked', 'P0'
            elif not available:
                state, priority = 'not_available', None
                reasons.add('not_observed_not_a_negative_safety_claim')
            else:
                different = comparison not in ('exact_agreement', 'normalized_agreement')
                if different and policy['allow_single_terminal_full_stop_difference'] and terminal_period_only(a, b):
                    different = False; reasons.add('single_terminal_full_stop_difference')
                if different: reasons.add('engine_disagreement')
                if scores and min(scores) < policy['minimum_line_confidence']: reasons.add('low_model_confidence')
                if comparison == 'occurrence_count_difference': reasons.add('repeated_field_occurrences')
                if different and re.findall(r'\d+(?:\.\d+)?', normalized(a)) != re.findall(r'\d+(?:\.\d+)?', normalized(b)):
                    reasons.add('numeric_disagreement')
                if different and re.findall(r'禁用|慎用|不宜|不得|不可|无|未', a) != re.findall(r'禁用|慎用|不宜|不得|不可|无|未', b):
                    reasons.add('negation_or_caution_disagreement')
                review = different or any(r in reasons for r in ('mixed_script_in_latin_word', 'low_model_confidence', 'repeated_field_occurrences'))
                state = 'review_required' if review else 'machine_checks_passed'
                priority = ('P0' if key in SAFETY or reasons & {'numeric_disagreement', 'negation_or_caution_disagreement'} else
                            'P1' if key in IDENTITY else 'P2') if review else None
            eligible = state == 'machine_checks_passed'
            fields.append({'id': record['id']+':'+key, 'case_id': record['id'],
                'name_zh_candidate': record['name_zh_candidate'], 'etcm_source_record_id': record['etcm_source_record_id'],
                'field': key, 'machine_status': state, 'review_priority': priority,
                'reasons': sorted(reasons | errors), 'comparison': comparison,
                'minimum_model_confidence': min(scores) if scores else None,
                'human_review': field['human_review'], 'human_signoff': False,
                'eligible_for_research_preview': eligible,
                'allowed_use': policy['allowed_use'] if eligible else None,
                'stratum': stratum, 'sampling_group': group(key),
                'selected_engine': 'rapidocr' if eligible else None,
                'excerpt': a if eligible else None, 'previews': field['previews'],
                'alternatives': alternatives, 'sampled': False})
    samples = []
    strata = defaultdict(list)
    for field in fields:
        if field['eligible_for_research_preview']: strata[field['stratum']].append(field)
    for stratum, candidates in sorted(strata.items()):
        candidates.sort(key=lambda f: hashlib.sha256((policy['sampling_seed']+'|'+stratum+'|'+f['id']).encode()).hexdigest())
        for candidate in candidates[:policy['sample_per_stratum']]:
            candidate['sampled'] = True; samples.append(candidate['id'])
    queue = [f['id'] for f in sorted((f for f in fields if f['machine_status'] in ('review_required', 'blocked')),
                                    key=lambda f: (f['review_priority'], f['id']))]
    return {'schema_version': 1, 'kind': 'unreviewed_research_quality_batch',
        'policy': policy, 'human_signoff': False, 'fields': fields, 'review_queue': queue,
        'stratified_samples': samples, 'documents': data['documents'],
        'summary': {'field_slots': len(fields), 'machine_status_counts': dict(Counter(f['machine_status'] for f in fields)),
                    'review_queue_count': len(queue), 'sample_count': len(samples),
                    'human_reference_fields_completed': 0, 'approved_fields': 0,
                    'character_accuracy': None, 'manual_workload_reduction': None}}
