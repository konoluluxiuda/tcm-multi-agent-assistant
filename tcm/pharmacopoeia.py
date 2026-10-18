"""Unreviewed field candidates from manually scoped OCR; no production writes."""
import hashlib
import json
import math
from pathlib import Path
import re

FIELDS = {'来源': 'origin', '性味与归经': 'properties_meridians',
          '功能与主治': 'efficacy_indications', '用法与用量': 'usage',
          '注意': 'cautions', '贮藏': 'storage'}
HEAD = re.compile(r'【([^【】]{1,24})】')
MISSING = 'not_observed_in_selected_ocr'
MISSING_MESSAGE = '所选OCR片段未观察到该字段，不表示无禁忌、无注意事项或无相关记载。'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8*1024*1024), b''): h.update(chunk)
    return h.hexdigest()


def normalize(value):
    return ''.join(value.split()).casefold()


def extract_fields(lines, *, source_openers=('本品为',)):
    """Split on all bracket headings, including headings we don't export."""
    fields = {key: {'state': MISSING, 'message': MISSING_MESSAGE, 'occurrences': []}
              for key in (*FIELDS.values(), 'medicinal_part', 'pinyin', 'latin_drug_name')}
    issues = []
    # A second visible Chinese/Pinyin/Latin heading signals a region spilling into another entry.
    first_heading = next((i for i,l in enumerate(lines) if HEAD.search(l['text'])), len(lines))
    for i in range(first_heading+1, len(lines)):
        if (i>=2 and re.fullmatch(r'[A-Z][A-Z\s]{4,}', lines[i]['text'].strip()) and
                re.fullmatch(r'[A-Za-z\s]+', lines[i-1]['text'].strip()) and
                re.fullmatch(r'[\u4e00-\u9fff]{2,8}', ''.join(lines[i-2]['text'].split()))):
            issues.append({'code':'possible_multiple_entries','blocking':True,'evidence':lines[i-2:i+1]})
            lines=lines[:i-2]
            break
    def add(key, text, evidence, boundary='next_heading'):
        if not text.strip():
            issues.append({'code':'empty_field_block','field':key,'blocking':True,'evidence':evidence})
            if not fields[key]['occurrences']:
                fields[key].update(state='unresolved_ocr_value',message='观察到字段标题但内容未提取，需核对原图。')
            return
        fields[key]['state'] = 'candidate'
        fields[key].pop('message', None)
        fields[key]['occurrences'].append({'raw_text': text.strip(), 'evidence': evidence,
                                          'end_boundary': boundary, 'human_review': 'pending'})
    # Intro and title identities are limited to the region before the first heading.
    intro = []
    for line in lines:
        if HEAD.search(line['text']): break
        intro.append(line)
    start = next((i for i,l in enumerate(intro) if any(opener in l['text'] for opener in source_openers)), None)
    if start is not None:
        origin = intro[start:]
        add('origin', '\n'.join(l['text'] for l in origin), origin)
        text = ''.join(l['text'] for l in origin)
        match = re.search(r'的(干燥(?:全草|藤茎|果皮|块根))', text)
        if match:
            add('medicinal_part', match.group(1), origin)
        elif '干燥' in text:
            fields['medicinal_part'].update(state='unresolved_ocr_value',message='来源描述未匹配本试验的部位词表，需核对原文。')
            issues.append({'code':'medicinal_part_unresolved','blocking':True,'evidence':origin})
        identities = intro[:start]
        if len(identities) >= 2:
            add('pinyin', identities[1]['text'], [identities[1]])
        latin = [l for l in identities[2:] if re.search('[A-Z]', l['text'])]
        if latin:
            add('latin_drug_name', '\n'.join(l['text'] for l in latin), latin)
    else:
        issues.append({'code': 'origin_boundary_unresolved', 'blocking': True})
    active, texts, evidence = None, [], []
    def flush(boundary):
        if active in FIELDS:
            add(FIELDS[active], '\n'.join(texts), evidence[:], boundary)
    for line in lines:
        text = line['text']
        headings = list(HEAD.finditer(text))
        if text.count('【') != len(headings):
            flush('malformed_heading')
            active, texts, evidence = None, [], []
            issues.append({'code': 'malformed_heading', 'blocking': True, 'evidence': [line]})
            continue
        if headings:
            prefix = text[:headings[0].start()]
            if active and prefix.strip(): texts.append(prefix); evidence.append(line)
            flush('next_heading')
            for index, heading in enumerate(headings):
                active = heading.group(1)
                end = headings[index+1].start() if index+1 < len(headings) else len(text)
                value = text[heading.end():end]
                texts, evidence = [value], [line]
                if index+1 < len(headings): flush('next_heading')
        elif active:
            texts.append(text); evidence.append(line)
    flush('selected_region_end')
    for key, field in fields.items():
        if len(field['occurrences']) > 1:
            issues.append({'code': 'multiple_field_occurrences', 'field': key, 'blocking': True})
        if any(o['end_boundary'] in ('malformed_heading','selected_region_end') for o in field['occurrences']):
            issues.append({'code': 'field_end_requires_review', 'field': key, 'blocking': False})
    return fields, issues


def selected_lines(suite, case, ocr_dir):
    result, fingerprints = [], {}
    for segment in case['segments']:
        label, number, column = segment['document'], segment['pdf_page'], segment['column']
        key = f'{label}-{number}-{column}'
        path = Path(ocr_dir)/(key+'.json')
        data = json.loads(path.read_text())
        meta = data['metadata']
        source = suite['documents'][label]
        if (meta['key'] != key or meta['pdf_page'] != number or meta['column'] != column or
                meta['source']['sha256'] != source['sha256'] or meta['source']['path'] != source['path'] or
                meta['printed_page'] != source['printed_pages'][str(number)]):
            raise ValueError('OCR source metadata mismatch: '+key)
        fingerprints[path.name] = digest(path)
        low, high = segment['y_range']
        for index, line in enumerate(data['lines']):
            box, score, center = line['pdf_box'], line['score'], line['center_y_fraction']
            if (not isinstance(line['text'], str) or len(box) != 4 or
                any(len(point)!=2 or any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 for v in point) for point in box) or
                not math.isfinite(score) or not 0<=score<=1 or not math.isfinite(center) or not 0<=center<=1):
                raise ValueError('Invalid OCR line geometry or score: '+key)
            if low <= center <= high:
                result.append({'text':line['text'], 'score':score, 'pdf_box':box,
                    'source_key':key, 'line_index':index, 'document':label,
                    'source_sha256':source['sha256'], 'pdf_page':number,
                    'printed_page':meta['printed_page'], 'column':column})
    return result, fingerprints


def build_candidates(suite, ocr_dir, annotations):
    records, inputs = [], {}
    for case in suite['cases']:
        lines, hashes = selected_lines(suite, case, ocr_dir)
        inputs.update(hashes)
        # expected_fragments are deliberately not read by the extractor.
        fields, issues = extract_fields(lines)
        pinyin = fields['pinyin']['occurrences']
        if not pinyin or normalize(pinyin[0]['raw_text']) != normalize(case['id']):
            issues.append({'code':'pinyin_identity_mismatch','field':'pinyin','blocking':True,
                           'expected_candidate':case['id'], 'evidence':pinyin[0]['evidence'] if pinyin else []})
        for note in annotations:
            if note['case_id'] != case['id']: continue
            matches=[l for l in lines if l['source_key']==note['source_key'] and note['observed_fragment'] in l['text']]
            if not matches: raise ValueError('Review annotation no longer matches OCR: '+case['id'])
            issues.append({'code':'visual_ocr_review_flag','blocking':True,'annotation':note,
                           'evidence':matches, 'action':'retain_original_do_not_autocorrect'})
        low_lines=[l for f in fields.values() for o in f['occurrences'] for l in o['evidence'] if l['score']<.9]
        if low_lines: issues.append({'code':'low_confidence_field_evidence','blocking':False,'evidence':low_lines})
        records.append({'id':case['id'], 'name_zh_candidate':case['name_zh_candidate'],
            'etcm_source_record_id':case['etcm_source_record_id'], 'mapping_status':case['mapping_status'],
            'review_status':'quarantined' if any(i['blocking'] for i in issues) else 'pending_human_review',
            'human_signoff':False, 'fields':fields, 'issues':issues})
    return {'schema_version':1,'kind':'pharmacopoeia_field_candidates', 'documents':suite['documents'],
            'scope':'manually selected four OCR regions, no canonical entity mapping',
            'human_signoff':False,'ocr_inputs_sha256':inputs,'records':records}


def query_candidates(data, name, field):
    if field not in (*FIELDS.values(),'medicinal_part','pinyin','latin_drug_name'):
        raise ValueError('Unsupported candidate field')
    if not isinstance(name,str) or not name.strip(): raise ValueError('Name is required')
    records=[r for r in data['records'] if normalize(name) in
             {normalize(r['id']),normalize(r['name_zh_candidate']),r['etcm_source_record_id']}]
    if len(records)!=1:
        return {'status':'ambiguous_entity' if records else 'not_found','records':[],
                'candidate_ids':[r['id'] for r in records]}
    record=records[0]
    return {'status':'unreviewed_candidate','id':record['id'],'review_status':record['review_status'],
            'human_signoff':False,'field':field,'result':record['fields'][field], 'issues':record['issues']}
