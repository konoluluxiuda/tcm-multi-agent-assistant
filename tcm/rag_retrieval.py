"""Release-bound document search. No benchmark labels, providers or source writes.

Object/field constraints come only from the question and the shared catalogue.
All methods share these constraints; a dense score never certifies an answer.
"""
from pathlib import Path
import json
import re
import sqlite3
import time

from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_retrieval import ResearchRetriever, INTENTS, name_spans, name_key, terms
from tcm.mineru_guards import PROCESS_PREFIXES, field_body, reference_target
from tcm.mineru_revisions import fingerprint
from tcm.rag_benchmark import inside, pointer, read

METHODS = ('exact', 'bm25', 'dense', 'hybrid')
POLICY = {'version': 'r3-query-v1', 'max_question_chars': 500, 'max_k': 20,
          'candidate_k': 20, 'rrf_constant': 60, 'appendices': False,
          'dense_acceptance_threshold': None, 'semantic_results': 'candidates_not_exhaustive',
          'identity_verified': False}


def interpret(question, names):
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 500:
        raise ValueError('Question must contain 1–500 characters')
    # Later presentation instructions must not introduce another object, e.g.
    # “保留酒当归限定”. They remain in the actual retrieval query and source text.
    subject = re.split(r'[。；;？?]|[，,](?=请|只|仅|保留|不要|不得|分别|某项|后面|原文|不确认)', question)[0]
    spans = name_spans(subject, names)
    entities = sorted({n for _, _, n in spans})
    variants = []
    normalized = name_key(subject)
    for a, _, n in spans:
        prefix = next((p for p in PROCESS_PREFIXES if normalized[:a].endswith(p)), None)
        if prefix:
            variants.append(prefix+n)
    # A catalogue substring is not a recognized chemical/preparation name.
    invalid_context = [n for _,end,n in spans if re.match(r'^(?:素|碱|酮|苷|曲|[a-z0-9])', normalized[end:])]
    uncertain = bool(re.search(r'也可能|还没确定|尚未确定|不确定|还是|尚未明确|未确定', subject))
    quoted = re.findall(r'【([^】]+)】', question)
    fields = [f for f in quoted if f in INTENTS or f in ('origin','性味')]
    strict = bool(quoted and re.search(r'独立|只查|仅查|没有这个字段', question))
    if not fields:
        # 出处/原始记录位置 is provenance, never the botanical origin column.
        fields = [f for f, aliases in INTENTS.items()
                  if f != 'origin' and any(a in question for a in aliases)]
        if re.search(r'来源段|基原|药典来源|什么植物|哪些植物|入药部位|药用部位', question):
            fields.append('origin')
        if '贮藏' in fields and re.search(r'贮藏要注意|保存要注意|存放要注意', question):
            fields = [f for f in fields if f != '注意']
    required = sorted(set(fields))
    groups = [[f] for f in required]
    if '性味与归经' in fields and not strict:
        fields.append('性味')
        groups = [['性味与归经','性味'] if f == '性味与归经' else [f] for f in required]
    # Explicit form selection only. Default retains source scopes, not an
    # inferred approval of monograph/prepared-slice equivalence.
    scope = 'prepared_slices' if re.search(r'只(?:查|看|要)饮片', question) else None
    if re.search(r'只(?:查|看|要)(?:原药材|生药)', question):
        scope = 'monograph'
    return {'version': POLICY['version'], 'names': entities, 'fields': sorted(set(fields)),
            'required_field_groups':groups,
            'scope': scope, 'independent_field_only': strict,
            'clarification_required': bool(variants or invalid_context or (uncertain and len(entities) > 1)),
            'unresolved_preparations': sorted(set(variants)), 'unresolved_name_contexts':invalid_context}


class ReleaseSearch:
    """Open one immutable release; call close() after a task/experiment batch.

    Performs runtime binding checks without opening reference answers. The build
    and evaluation CLIs additionally run the complete R1 source-anchor check.
    """
    def __init__(self, release, dense=None):
        self.root = Path(release).resolve()
        self.release = read(self.root/'release.json')
        if (self.release['status'] != 'ready_research_only' or
                self.release['content_id'] != fingerprint({k:v for k,v in self.release.items() if k != 'content_id'})):
            raise ValueError('Release identity changed')
        for name in ('sources.private.json', 'index/manifest.json', 'index/research.sqlite3'):
            if digest(inside(self.root, name)) != self.release['artifacts'][name]:
                raise ValueError('Release runtime artifact changed')
        wal = self.root/'index/research.sqlite3-wal'
        if wal.exists() and wal.stat().st_size:
            raise ValueError('Index has unregistered WAL changes')
        self.registry = read(self.root/'sources.private.json')
        for role in ('herbs', 'enrichment'):
            source = self.registry['sources'][role]
            path = Path(source['path'])
            wal = Path(str(path)+'-wal')
            if wal.exists() and wal.stat().st_size:
                raise ValueError('Source has unregistered WAL changes')
            if digest(path) != source['sha256'] or source['sha256'] != self.release['sources_sha256'][role]:
                raise ValueError('Registered source changed')
        self.lexical = ResearchRetriever(self.root/'index')
        self.dense = dense
        if dense is not None and dense.receipt['index_sha256'] != self.lexical.receipt['index_sha256']:
            self.lexical.close()
            raise ValueError('Dense cache is bound to another release index')
        self.rows = {r['chunk_id']:r for r in self.lexical.connection.execute('SELECT * FROM chunks')}
        self.quarantine = {(q['source_record_id'], q['entry_id']) for q in
                           self.release['sidecars']['mapping']['quarantined_candidates']}
        self.documents = []
        with sqlite3.connect(Path(self.registry['config']['herbs']).resolve().as_uri()+'?mode=ro', uri=True) as db:
            for (doc,) in db.execute('SELECT document FROM records WHERE state=? AND dataset_version=?',
                                     ('valid', self.release['herbs']['dataset_version'])):
                self.documents.append(json.loads(doc))
        with sqlite3.connect(Path(self.registry['config']['enrichment']).resolve().as_uri()+'?mode=ro', uri=True) as db:
            self.links = [tuple(r) for r in db.execute('SELECT herb_id,entry_id,name_zh FROM name_candidates')]

    def close(self):
        self.lexical.close()

    def structured(self, question, request):
        if not re.search(r'ETCM', question, re.I):
            return [], None
        fields = []
        if re.search(r'拉丁|latin', question, re.I): fields.append('latin_name')
        if re.search(r'药用部位|入药部位|medicinal.part', question, re.I): fields.append('medicinal_part')
        if not fields:
            return [], 'structured_field_not_resolved'
        explicit = [d for d in self.documents if re.search(
            r'(?<![A-Za-z0-9])'+re.escape(d['fields'].get('name_pinyin', {}).get('value') or '\x00')+r'(?![A-Za-z0-9])',
            question, re.I)]
        if not explicit:
            linked = {sid for sid, eid, name in self.links if name in request['names'] and (sid,eid) not in self.quarantine}
            explicit = [d for d in self.documents if d['source_record_id'] in linked]
        if len(explicit) != 1:
            return [], 'structured_source_requires_selection'
        doc = explicit[0]; sid = doc['source_record_id']
        if any(sid == s and e in {r['entry_id'] for r in self.rows.values() if r['name'] in request['names']}
               for s,e in self.quarantine):
            return [], 'quarantined_name_candidate'
        hits = []
        for field in fields:
            value = doc['fields'].get(field, {})
            if value.get('state') != 'present' or not value.get('source'):
                continue
            source = value['source']
            path = inside(self.registry['config']['source_root'], source['relative_file_path'])
            if digest(path) != source['file_sha256'] or pointer(read(path), source['json_pointer']) != source['raw_value']:
                raise ValueError('Raw structured evidence changed')
            hits.append({'kind':'etcm_json', 'source_record_id':sid, 'label':field,
                         'value':value['value'], 'raw_value':source['raw_value'],
                         'relative_file_path':source['relative_file_path'], 'file_sha256':source['file_sha256'],
                         'json_pointer':source['json_pointer'], 'identity_verified':False})
        return hits, None if len(hits) == len(fields) else 'structured_field_missing'

    def search(self, question, method='bm25', k=10, *, names=None, fields=None):
        if method not in METHODS or type(k) is not int or not 1 <= k <= POLICY['max_k']:
            raise ValueError('Unknown method or invalid k')
        started = time.monotonic()
        request = interpret(question, self.lexical.names)
        if names is not None or fields is not None:
            # Trusted task tools supply immutable goal constraints before top-k.
            # A rewritten query may narrow them, never introduce other objects.
            if (names is not None and (not isinstance(names,list) or not names or
                    any(n not in self.lexical.names for n in names)) or
                    fields is not None and (not isinstance(fields,list) or not fields or
                    any(f not in INTENTS and f!='性味' for f in fields))):
                raise ValueError('Unknown task search filters')
            if names is not None:
                if set(request['names'])-set(names):raise ValueError('Query exceeds task objects')
                request['names']=request['names'] or sorted(set(names))
            if fields is not None:
                allowed_fields=set(fields)
                if '性味与归经' in allowed_fields:allowed_fields.add('性味')
                if set(request['fields'])-allowed_fields:raise ValueError('Query exceeds task fields')
                if not request['fields']:
                    request['fields']=sorted(allowed_fields)
                    request['required_field_groups']=[['性味与归经','性味'] if f=='性味与归经' else [f] for f in fields]
            request['host_constrained']=True
        result = {'method':method, 'question':question, 'request':request, 'hits':[], 'structured_hits':[],
                  'release_content_id':self.release['content_id'], 'index_sha256':self.lexical.receipt['index_sha256'],
                  'external_calls':0, 'generation_calls':0, 'research_only':True,
                  'identity_verified':False, 'exhaustive':False, 'encoding_ms':0}
        def finish(status):
            return {**result, 'status':status, 'latency_ms':round((time.monotonic()-started)*1000, 3)}
        if request['clarification_required']:
            return finish('clarification_required')
        result['structured_hits'], problem = self.structured(question, request)
        if problem in ('structured_source_requires_selection', 'quarantined_name_candidate'):
            result['reason'] = problem
            return finish('clarification_required')
        result['structured_problem'] = problem
        names, fields, scope = request['names'], request['fields'], request['scope']
        allowed = [r for r in self.rows.values() if not r['scope'].startswith('appendix:')
                   and (not names or r['name'] in names) and (not fields or r['label'] in fields)
                   and (not scope or r['scope'] == scope)]
        result['pool_size'] = len(allowed)
        if names and fields:
            observed = {(r['name'],r['label']) for r in allowed}
            result['missing_fields'] = [{'name':n,'label':g[0]} for n in names
                                        for g in request['required_field_groups']
                                        if not any((n,f) in observed for f in g)]
        if not allowed:
            return finish('missing_field' if names and fields else 'no_match')
        if names and fields and any(sum(r['name']==n and r['label'] in g for r in allowed)>1
                                   for n in names for g in request['required_field_groups']):
            result['reason'] = 'duplicate_field_scope'
            return finish('clarification_required')
        # Remove unresolved references consistently before ranking/top-k.
        valid = {r['chunk_id']:r for r in allowed if field_body(r['text']) and not reference_target(r['text'])}
        result['unresolved_count'] = len(allowed)-len(valid)
        if not valid:
            return finish('reference_unresolved')
        ids = set(valid)
        lexical_rank = []
        if method in ('bm25','hybrid'):
            expression = ' OR '.join('"'+t+'"' for t in sorted(set(terms(question))))
            if expression:
                # Full FTS matches, then shared request filters, then bounded top-k.
                matches = self.lexical.connection.execute(
                    'SELECT c.chunk_id,bm25(fts,3.0,2.0,1.0) AS rank FROM fts '
                    'JOIN chunks c ON c.rowid=fts.rowid WHERE fts MATCH ? ORDER BY rank,c.chunk_id', (expression,))
                lexical_rank = [(r['chunk_id'],-float(r['rank'])) for r in matches if r['chunk_id'] in ids][:20]
        dense_rank = []
        if method in ('dense','hybrid'):
            if self.dense is None:
                raise ValueError('Compatible local dense backend required')
            selected = [i for i,cid in enumerate(self.dense.identifiers) if cid in ids]
            if selected:
                before = time.monotonic()
                vector = self.dense.encoder.encode([question], query=True)[0]
                result['encoding_ms'] = round((time.monotonic()-before)*1000, 3)
                result['query_truncated'] = bool(self.dense.encoder.last_truncated[0])
                dense_rank = [(self.dense.identifiers[i],score) for i,score in self.dense.rank(vector,20,selected)]
        if method == 'exact':
            ranking = [(cid,None) for cid in sorted(ids)] if names and fields else []
        elif method == 'bm25':
            ranking = lexical_rank
        elif method == 'dense':
            ranking = dense_rank
        else:
            scores = {}
            for ranking in (lexical_rank,dense_rank):
                for rank,(cid,_) in enumerate(ranking,1): scores[cid] = scores.get(cid,0)+1/(60+rank)
            ranking = sorted(scores.items(), key=lambda p:(-p[1],p[0]))
        for cid,score in ranking[:k]:
            hit = self.lexical.hit(valid[cid]);hit.update(score=score,kind='pdf_field')
            result['hits'].append(hit)
        if not result['hits']:
            return finish('reference_unresolved' if result['unresolved_count'] else 'no_match')
        # Unrequested reference-only fields elsewhere in an open search pool
        # do not make the returned candidates incomplete. Explicit field reads
        # must still report a missing/reference-only requested value.
        if problem or result.get('missing_fields') or (result['unresolved_count'] and names and fields):
            return finish('partial_evidence')
        # Open semantic results are candidates, without an acceptance threshold.
        return finish('evidence_found' if names and fields else 'candidates')


class MultiEntryQdrant:
    """One exclusive local Qdrant owner, verified before and after a batch.

    Unlike the historical single-herb adapter, accepts the full shared bounded
    request pool. No network mode, and no caller-supplied paths/SQL per query.
    """
    def __init__(self, dense, generation):
        from qdrant_client import QdrantClient
        from tcm.qdrant_dense import check_receipt, locked_generation
        self.dense = dense
        self.encoder, self.receipt = dense.encoder, dense.receipt
        self.items, self.identifiers = dense.items, dense.identifiers
        self.generation = Path(generation).resolve()
        self.qreceipt = read(self.generation/'receipt.json')
        check_receipt(self.qreceipt, self.receipt)
        self.lock = locked_generation(self.generation)
        self.lock.__enter__()
        self.client = None
        try:
            self.verify_files()
            self.client = QdrantClient(path=str(self.generation/'store'))
            if self.client.count('pharmacopoeia',exact=True).count != self.receipt['count']:
                raise ValueError('Qdrant count changed')
        except Exception:
            if self.client is not None: self.client.close()
            self.lock.__exit__(None,None,None)
            raise

    def verify_files(self):
        for name,sha in self.qreceipt['files'].items():
            if digest(inside(self.generation,name)) != sha:
                raise ValueError('Qdrant generation changed')

    def close(self):
        try:
            self.client.close()
            self.verify_files()
        finally:
            self.lock.__exit__(None,None,None)

    def rank(self, vector, k, allowed):
        from qdrant_client import models
        from tcm.qdrant_dense import point_id
        if type(k) is not int or not 1 <= k <= 20 or allowed is None:
            raise ValueError('Explicit bounded candidate indices and k=1–20 required')
        if any(type(i) is not int or not 0 <= i < len(self.items) for i in allowed):
            raise ValueError('Invalid candidate index')
        if not allowed: return []
        items = {self.identifiers[i]:i for i in allowed}
        filters = models.Filter(must=[
            models.FieldCondition(key='index_sha256',match=models.MatchValue(value=self.receipt['index_sha256'])),
            models.FieldCondition(key='research_scope',match=models.MatchValue(value=True)),
            models.FieldCondition(key='chunk_id',match=models.MatchAny(any=sorted(items)))])
        points = self.client.query_points('pharmacopoeia',query=vector.tolist(),query_filter=filters,
                                          limit=len(items),with_payload=True,with_vectors=False).points
        result = []
        for p in points:
            cid = p.payload['chunk_id']
            if cid not in items or str(p.id) != point_id(cid):
                raise ValueError('Qdrant returned unbound evidence')
            expected = self.items[items[cid]]
            if any(p.payload[key] != expected[key] for key in ('entry_id','name','label','scope')):
                raise ValueError('Qdrant payload differs from verified cache')
            result.append((items[cid],float(p.score)))
        return sorted(result,key=lambda p:(-p[1],self.identifiers[p[0]]))[:k]
