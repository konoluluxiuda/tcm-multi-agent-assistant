"""Source-grounded benchmark candidates and frozen research references.

Never query a retriever or infer an answer from the tested index. Authors select
raw anchors explicitly; freezing validates those assignments, not clinical truth.
"""
from collections import Counter
from pathlib import Path
import hashlib
import json
import math
import sqlite3

from tcm.mineru_pharmacopoeia import digest, segment_text
from tcm.mineru_revisions import fingerprint


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def inside(root, relative):
    root = Path(root).resolve()
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError('Source path must be relative')
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('Source path escaped its root or is missing')
    return path


def pointer(document, value):
    if not isinstance(value, str) or not value.startswith('/'):
        raise ValueError('JSON pointer is required')
    for part in value[1:].split('/'):
        part = part.replace('~1', '/').replace('~0', '~')
        if isinstance(document, list):
            if not part.isdecimal() or str(int(part)) != part:
                raise ValueError('Noncanonical JSON array pointer')
            document = document[int(part)]
        else:
            document = document[part]
    return document


class RawCorpus:
    def __init__(self, base, source_root, pdfs):
        self.base, self.source_root = Path(base), Path(source_root)
        self.summary = read(self.base/'summary.json')
        for name in ('entries', 'blocks'):
            if digest(self.base/(name+'.json')) != self.summary[name+'_sha256']:
                raise ValueError('Raw corpus receipt changed')
        self.ordered = read(self.base/'blocks.json')
        self.blocks = {b['id']:b for b in self.ordered}
        self.entries = read(self.base/'entries.json')['entries']
        self.by_id = {e['id']:e for e in self.entries}
        self.positions = {b['id']:i for i,b in enumerate(self.ordered)}
        if len(self.blocks) != len(self.ordered) or len(self.by_id) != len(self.entries):
            raise ValueError('Duplicate source identity')
        self.pdfs = {k:Path(v) for k,v in pdfs.items()}
        self.pdf_hashes = {k:digest(v) for k,v in self.pdfs.items()}
        import pymupdf
        self.pdf_pages = {}
        for name,path in self.pdfs.items():
            with pymupdf.open(path) as pdf: self.pdf_pages[name] = pdf.page_count
        for block in self.ordered:
            source = block['source'];page = source['source_pdf_page'];box = block['raw']['bbox']
            if (source['source_sha256'] != self.pdf_hashes[source['source_document']] or
                    type(page) is not int or not 1 <= page <= self.pdf_pages[source['source_document']] or
                    len(box) != 4 or any(type(v) not in (int,float) or not math.isfinite(v) or not 0 <= v <= 1000 for v in box) or
                    box[0] > box[2] or box[1] > box[3]):
                raise ValueError('Raw PDF block has invalid document/page/geometry')
        self.files = {}

    def pdf_anchor(self, spec):
        b = self.blocks[spec['block_id']]
        if (type(spec['start']) is not int or type(spec['end']) is not int or
                spec['start'] >= spec['end'] or spec['start'] < 0):
            raise ValueError('Invalid source offsets')
        text = segment_text(spec, self.blocks)
        if len(text) != spec['end']-spec['start']:
            raise ValueError('Source span is truncated')
        if b['source']['source_sha256'] != self.pdf_hashes[b['source']['source_document']]:
            raise ValueError('PDF source changed')
        expected = hashlib.sha256(text.encode()).hexdigest()
        if spec.get('text_sha256') != expected or spec.get('block_sha256') != fingerprint(b):
            raise ValueError('Authored raw span changed')
        entry = self.by_id[spec['entry_id']]
        if b['id'] not in entry['block_ids']:
            raise ValueError('Cross-entry raw evidence')
        anchor = {k:spec[k] for k in ('entry_id','label','scope','block_id','start','end')}
        anchor.update({k:spec[k] for k in ('text_source','item_index') if k in spec})
        anchor.update(kind='pdf_block', source=b['source'], bbox_0_1000=b['raw']['bbox'],
                      text_sha256=expected, block_sha256=fingerprint(b))
        anchor['anchor_id'] = 'A-'+fingerprint(anchor)[:32]
        return anchor

    def json_anchor(self, spec):
        path = inside(self.source_root, spec['relative_file_path'])
        sha = digest(path)
        if sha != spec['file_sha256']:
            raise ValueError('Authored ETCM source changed')
        self.files[spec['relative_file_path']] = sha
        value = pointer(read(path), spec['json_pointer'])
        if fingerprint(value) != spec['value_sha256']:
            raise ValueError('Authored JSON value changed')
        sid = 'etcm2-local:'+hashlib.sha256(spec['relative_file_path'].encode()).hexdigest()
        if spec['source_record_id'] != sid:
            raise ValueError('Structured source identity mismatch')
        anchor = {k:spec[k] for k in ('relative_file_path','file_sha256','json_pointer',
                                     'value_sha256','source_record_id','label')}
        anchor.update(kind='etcm_json')
        anchor['anchor_id'] = 'A-'+fingerprint(anchor)[:32]
        return anchor

    def anchor(self, spec):
        if spec['kind'] == 'pdf_block': return self.pdf_anchor(spec)
        if spec['kind'] == 'etcm_json': return self.json_anchor(spec)
        raise ValueError('Unknown source anchor kind')

    def absence(self, spec):
        """Scan a frozen original reading-order interval, not parser field lists."""
        entry = self.by_id[spec['entry_id']]
        i = self.positions[spec['start_block_id']]
        j = self.positions[spec['end_before_block_id']]
        if spec['start_block_id'] != entry['title_block_ids'][0] or j <= i:
            raise ValueError('Invalid entry interval')
        successor = self.entries[self.entries.index(entry)+1]
        if spec['end_before_block_id'] != successor['title_block_ids'][0]:
            raise ValueError('Absence interval is not bounded by the next entry')
        items = self.ordered[i:j]
        if fingerprint(items) != spec['interval_sha256']:
            raise ValueError('Authored absence scope changed')
        label = '【'+spec['label']+'】'
        if any(label in b['raw'].get('text','') for b in items):
            raise ValueError('Claimed missing label occurs in raw source interval')
        return {**spec, 'block_count':len(items),
                'meaning':'label_absent_in_this_frozen_OCR_entry_interval_not_absence_in_reality'}


def candidates(corpus):
    rows = []
    for entry in corpus.entries:
        for field in entry['fields']:
            segments = [s for s in field['segments'] if 'start' in s and s['end'] > s['start']]
            if not segments: continue
            spans = []
            for segment in segments:
                block = corpus.blocks[segment['block_id']]
                spans.append({**segment, 'text_sha256':hashlib.sha256(segment_text(segment,corpus.blocks).encode()).hexdigest(),
                              'block_sha256':fingerprint(block), 'entry_id':entry['id'],
                              'label':field['label'], 'scope':field['scope'], 'kind':'pdf_block'})
            value = {'entity':entry['name_zh_candidate'], 'entry_id':entry['id'],
                     'label':field['label'], 'scope':field['scope'], 'source_spans':spans}
            rows.append({'candidate_id':'C-'+fingerprint(value)[:32], **value,
                'question_template':entry['name_zh_candidate']+'的'+field['label']+'原文及出处是什么？',
                'status':'candidate_pending_review','human_signoff':False,
                'raw_flags':entry['flags'], 'usable_as_gold':False})
    return rows


def freeze(seeds, corpus):
    if seeds['source_blocks_sha256'] != corpus.summary['blocks_sha256']:
        raise ValueError('Benchmark targets another raw corpus')
    references = []; seen = set(); groups = {'development':set(),'reserved':set()}
    for case in seeds['cases']:
        if case['case_id'] in seen or case['split'] not in groups:
            raise ValueError('Duplicate case or unknown split')
        seen.add(case['case_id'])
        if not case['question'].strip() or case.get('human_signoff') is not False:
            raise ValueError('Invalid author question/review state')
        if case['review_status'] != 'ai_raw_source_checked_pending_human_signoff':
            raise ValueError('References require an explicit raw-source review record')
        titles = [corpus.pdf_anchor(s) for s in case['title_anchors']]
        relevant = [corpus.anchor(s) for s in case['relevant_anchors']]
        excluded = [corpus.pdf_anchor(s) for s in case.get('excluded_anchors',[])]
        absence = corpus.absence(case['absence_witness']) if case.get('absence_witness') else None
        actual_groups = {a.get('entry_id',a.get('source_record_id')) for a in titles+relevant}
        if not actual_groups or set(case['group_ids']) != actual_groups:
            raise ValueError('Case grouping must cover every source entity')
        groups[case['split']].update(actual_groups)
        status = case['expected_status']
        if (status == 'evidence_found' and not relevant or
                status == 'missing_field' and (not absence or relevant) or
                status == 'clarification_required' and len({a['entry_id'] for a in titles}) < 2 or
                status not in ('evidence_found','missing_field','clarification_required')):
            raise ValueError('Expected outcome lacks its original-source witness')
        references.append({**{k:v for k,v in case.items() if k not in
            ('title_anchors','relevant_anchors','excluded_anchors','absence_witness')},
            'title_anchors':titles,'relevant_anchors':relevant,'excluded_anchors':excluded,
            'absence_witness':absence,'reference_kind':'authored_raw_source_reference',
            'relevance_complete':False,'clinical_correctness_claim':False})
    if groups['development'] & groups['reserved']:
        raise ValueError('Source entity leakage between development and reserved cases')
    counts = dict(Counter(c['expected_status'] for c in references))
    splits = dict(Counter(c['split'] for c in references))
    if counts != seeds['expected_counts'] or splits != seeds['expected_splits']:
        raise ValueError('Frozen class/split counts do not match protocol')
    return {'version':'rag-source-reference-v1', 'cases':references,
            'source_blocks_sha256':corpus.summary['blocks_sha256'],
            'reference_counts':counts, 'splits':splits, 'human_signoff':False,
            'independent_user_set':False, 'reserved_status':'not_run_for_retrieval_or_generation',
            'kind':'author_AI_written_source_checked_research_reference',
            'not_a_model_or_retrieval_score':True}


def check_references(references, corpus):
    """Revalidate frozen values against raw sources without rewriting labels."""
    seed = {k:references[k] for k in ('source_blocks_sha256',)}
    seed.update(cases=references['cases'], expected_counts=references['reference_counts'],
                expected_splits=references['splits'])
    rebuilt = freeze(seed, corpus)
    if rebuilt != references:
        raise ValueError('Frozen reference annotations changed')
    return {'status':'passed','cases':len(references['cases']),
            'kind':'source_anchor_and_contract_integrity_only','retrieval_or_answer_scores':None,
            'reserved_retrieval_executed':False,'human_signoff':False}


def index_mapping(references, index):
    """Locate anchors for a version; mapping coverage is NOT retrieval recall."""
    with sqlite3.connect((Path(index)/'research.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute('SELECT chunk_id,entry_id,label,scope,payload FROM chunks')]
    mappings = []
    for case in references['cases']:
        for anchor in case['relevant_anchors']:
            if anchor['kind'] != 'pdf_block': continue
            matching = []
            for row in rows:
                if any(row[k] != anchor[k] for k in ('entry_id','label','scope')): continue
                evidence = json.loads(row['payload'])['evidence']
                if any(all(e.get(k) == anchor.get(k) for k in
                    ('block_id','start','end','text_source','item_index')) for e in evidence):
                    if not any(e.get('block_id') == x['block_id'] for e in evidence
                               for x in case['excluded_anchors']):
                        matching.append(row['chunk_id'])
            mappings.append({'case_id':case['case_id'],'anchor_id':anchor['anchor_id'],
                             'chunk_ids':matching,'status':'mapped' if matching else 'not_mapped'})
    return {'kind':'structural_anchor_mapping_not_retrieval_evaluation','mappings':mappings,
            'mapped':sum(m['status']=='mapped' for m in mappings),'total':len(mappings),
            'recall':None,'model_calls':0,'reserved_retrieval_executed':False}
