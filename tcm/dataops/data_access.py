"""Read-only, parameterized access to existing herb, sidecar and document catalogs."""
from contextlib import ExitStack, closing
import hashlib
import json
from pathlib import Path
import sqlite3

from tcm.dataops.contracts import LABELS, Query
from tcm.etcm_identity import compare


class SourceChanged(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def fingerprint(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def token(path):
    p = Path(path)
    return tuple((str(f), f.stat().st_ino, f.stat().st_size, f.stat().st_mtime_ns)
                 for f in (p, Path(str(p) + '-wal')) if f.exists())


def unrequested_inspection():
    """A goal without document fields makes no cross-source mapping claim."""
    return {'status': 'not_requested', 'candidates': [], 'fields': {},
            'comparison': None, 'identity_verified': False}


class Sources:
    def __init__(self, herbs, enrichment, index, dataset_version=None):
        self.paths = [Path(herbs).resolve(), Path(enrichment).resolve(),
                      Path(index).resolve() / 'research.sqlite3', Path(index).resolve() / 'manifest.json']
        self.version = dataset_version

    def __enter__(self):
        self.stack = ExitStack()
        try:
            self.tokens = [token(p) for p in self.paths]
            self.h, self.e, self.p = [self.stack.enter_context(closing(sqlite3.connect(p.as_uri() + '?mode=ro', uri=True)))
                                     for p in self.paths[:3]]
            for conn in (self.h, self.e, self.p):
                conn.row_factory = sqlite3.Row
                conn.execute('PRAGMA query_only=ON')
                conn.execute('BEGIN')
            if self.h.execute('PRAGMA user_version').fetchone()[0] != 1:
                raise ValueError('Herb schema mismatch')
            latest = self.h.execute('SELECT dataset_version FROM batches ORDER BY id DESC LIMIT 1').fetchone()
            if not latest or self.version and self.version != latest[0]:
                raise ValueError('Dataset version not available as current snapshot')
            self.version = latest[0]
            meta = {r[0]: json.loads(r[1]) for r in self.e.execute('SELECT key,value FROM metadata')}
            if meta.get('kind') != 'etcm_enrichment_v1' or meta.get('build_complete') is not True:
                raise ValueError('Incomplete enrichment catalog')
            receipt = json.loads(self.paths[3].read_text())
            if receipt['index_sha256'] != fingerprint(self.paths[2]):
                raise SourceChanged('Document index checksum mismatch')
            self.revision = {'dataset_version': self.version,
                             'sources': [fingerprint(p) for p in self.paths], 'rules': 'dataops-d1-v2-scope-aware'}
            self.assert_unchanged()
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *args):
        self.stack.close()

    def assert_unchanged(self):
        if self.tokens != [token(p) for p in self.paths]:
            raise SourceChanged('Source catalog changed; create a new workspace')

    def name_catalog(self):
        """Local aliases only; retain every candidate source, never approve identity."""
        aliases = {}
        for row in self.h.execute("SELECT source_record_id,document FROM records WHERE dataset_version=? AND state='valid'",
                                  (self.version,)):
            fields = json.loads(row['document'])['fields']
            for key in ('name_pinyin', 'latin_name', 'english_name'):
                name = fields.get(key, {}).get('value')
                if isinstance(name, str) and name.strip():
                    aliases.setdefault(name.strip(), set()).add(row['source_record_id'])
        for row in self.e.execute('SELECT c.name_zh,c.herb_id FROM name_candidates c JOIN herbs h '
                                  'ON h.source_record_id=c.herb_id WHERE h.dataset_version=?', (self.version,)):
            if row['name_zh']:
                aliases.setdefault(row['name_zh'], set()).add(row['herb_id'])
        self.assert_unchanged()
        return {name: sorted(ids) for name, ids in aliases.items()}

    def query(self, spec):
        spec = Query.model_validate(spec)
        from tcm.herbs import search_key
        resolutions = []
        resolved = set()
        for name in spec.names:
            hits = {r[0] for r in self.h.execute('SELECT n.source_record_id FROM names n JOIN records r '
                'USING(source_record_id,dataset_version) WHERE n.name_key=? AND n.dataset_version=? AND r.state=\'valid\'',
                (search_key(name), self.version))}
            hits.update(r[0] for r in self.e.execute('SELECT c.herb_id FROM name_candidates c JOIN herbs h '
                'ON h.source_record_id=c.herb_id WHERE c.name_zh=? AND h.dataset_version=?', (name, self.version)))
            # Candidate Chinese names are not identity approval; preserve every matching source.
            resolved.update(hits)
            resolutions.append({'name': name, 'source_ids': sorted(hits),
                                'status': 'not_found' if not hits else 'ambiguous' if len(hits) > 1 else 'name_candidate'})
        if len(resolved) > 50:
            raise ValueError('Name candidates exceed the 50-record budget; narrow the selection')
        clauses = ["dataset_version=?", "state='valid'"]
        params = [self.version]
        selected_ids = spec.source_ids or sorted(resolved)
        if spec.source_ids or spec.names:
            clauses.append('source_record_id IN (' + ','.join('?' for _ in selected_ids) + ')' if selected_ids else '0')
            params.extend(selected_ids)
        for f in spec.filters:
            # Field names are validated against a fixed allowlist, never taken as SQL.
            expr = "json_extract(document, '$.fields." + f.field + ".value')"
            if f.op == 'missing':
                clauses.append(f"({expr} IS NULL OR {expr}='')")
            elif f.op == 'equals':
                clauses.append(f'lower({expr})=lower(?)'); params.append(f.value)
            else:
                clauses.append(f"lower({expr}) LIKE lower(?) ESCAPE '\\'")
                params.append('%' + f.value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
        where = ' AND '.join(clauses)
        total = self.h.execute('SELECT count(*) FROM records WHERE ' + where, params).fetchone()[0]
        rows = self.h.execute('SELECT document FROM records WHERE ' + where +
                              ' ORDER BY source_record_id LIMIT ? OFFSET ?', [*params, spec.limit, spec.offset])
        docs = [json.loads(r[0]) for r in rows]
        groups = []
        if spec.group_by:
            field = "json_extract(document, '$.fields." + spec.group_by + ".value')"
            groups = [dict(r) for r in self.h.execute(
                f"SELECT {field} AS value,count(*) AS count FROM records WHERE {where} GROUP BY {field} ORDER BY count DESC,value", params)]
        self.assert_unchanged()
        returned = {d['source_record_id'] for d in docs}
        existing = {r[0] for r in self.h.execute('SELECT source_record_id FROM records WHERE dataset_version=? AND state=\'valid\'', (self.version,))}
        return {'total': total, 'returned': len(docs), 'offset': spec.offset,
                'next_offset': spec.offset + len(docs) if spec.offset + len(docs) < total else None,
                'unknown_source_ids': sorted(set(spec.source_ids) - existing),
                'name_resolution': resolutions,
                'unknown_names': [r['name'] for r in resolutions if r['status'] == 'not_found'],
                'selected_ids': sorted(returned), 'groups': groups, 'records': docs}

    def inspect(self, record, fields, *, candidate_id=None):
        sid = record['source_record_id']
        side = self.e.execute('SELECT file_sha256 FROM herbs WHERE source_record_id=? AND dataset_version=?', (sid, self.version)).fetchone()
        if not side or side[0] != record['source']['file_sha256']:
            return {'status': 'source_mismatch', 'candidates': [], 'fields': {}, 'comparison': None}
        candidates = [json.loads(r[0]) for r in self.e.execute(
            'SELECT evidence FROM name_candidates WHERE herb_id=? ORDER BY entry_id', (sid,))]
        unique = {c['entry_id']: c for c in candidates}
        candidates = list(unique.values())
        if candidate_id is not None:
            if candidate_id not in unique:
                raise ValueError('Candidate does not belong to this record')
            candidates = [unique[candidate_id]]
        if len(candidates) != 1:
            return {'status': 'ambiguous' if candidates else 'unmatched', 'candidates': candidates,
                    'fields': {}, 'comparison': None}
        candidate = candidates[0]
        entry = self.p.execute('SELECT name FROM entries WHERE entry_id=?', (candidate['entry_id'],)).fetchone()
        if not entry or entry[0] != candidate['name_zh']:
            return {'status': 'source_mismatch', 'candidates': candidates, 'fields': {}, 'comparison': None}
        wanted = sorted(set([LABELS[f] for f in fields] + ['origin']))
        rows = self.p.execute('SELECT * FROM chunks WHERE entry_id=? AND scope IN (\'monograph\',\'prepared_slices\') AND label IN (' +
                              ','.join('?' for _ in wanted) + ') ORDER BY field_index,chunk_id', [candidate['entry_id'], *wanted]).fetchall()
        found = {}
        for field in fields:
            parts = [r for r in rows if r['label'] == LABELS[field]]
            # Repeated labels may belong to different preparations. Never fuse them.
            value = parts[0]['raw_text'] if len(parts) == 1 else None
            evidence = [{'chunk_id': r['chunk_id'], 'entry_id': r['entry_id'],
                         'label': r['label'], 'scope': r['scope'],
                         'text': r['raw_text'], 'payload': json.loads(r['payload'])} for r in parts]
            status = ('ambiguous_field' if len(parts) > 1 else 'missing' if not value else
                      'candidate_value' if parts[0]['scope'] == 'monograph' else 'candidate_scope_value')
            found[field] = {'status': status, 'value': value or None,
                            'evidence': evidence}
        origin = [r for r in rows if r['label'] == 'origin' and r['scope'] == 'monograph']
        blocks = {r['chunk_id']: {'id': r['chunk_id'], 'text': r['raw_text'],
                                  'source': json.loads(r['payload']).get('evidence', [])} for r in origin}
        synthetic_entry = {'id': candidate['entry_id'],
                           'title_flags': [flag for r in origin for flag in json.loads(r['payload']).get('raw_flags', [])],
                           'fields': [{'scope': 'monograph', 'label': 'origin', 'segments': [
                               {'block_id': r['chunk_id'], 'start': 0, 'end': len(r['raw_text'])} for r in origin]}] if origin else []}
        comparison = compare(record, candidate, synthetic_entry, blocks)
        self.assert_unchanged()
        return {'status': comparison['status'], 'candidates': candidates, 'fields': found,
                'comparison': comparison, 'identity_verified': False}
