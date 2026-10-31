"""Immutable research release registration, local index build and benchmark binding.

Source files are referenced read-only, not copied or silently repaired. Consumers
must verify a release before use; registered paths are not portable backups.
"""
from contextlib import contextmanager
from pathlib import Path
import json
import os
import sqlite3

from tcm.mineru_pharmacopoeia import digest
from tcm.herbs import parse_herb
from tcm.mineru_revisions import fingerprint, field_text
from tcm.rag_benchmark import RawCorpus, candidates, check_references, freeze, index_mapping, inside, pointer, read, write

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {'base','revisions','index','herbs','enrichment','targets','source_root','pdf_high','pdf_full','seeds'}


@contextmanager
def readonly(path):
    path = Path(path).resolve()
    if Path(str(path)+'-wal').exists() and Path(str(path)+'-wal').stat().st_size:
        raise ValueError('Checkpoint source WAL before registering a file-hash release')
    db = sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite integrity check failed')
        yield db
    finally:
        db.close()


def metadata(db):
    return {r['key']:json.loads(r['value']) for r in db.execute('SELECT key,value FROM metadata')}


def inspect_herbs(config):
    files = {}; valid = []; sources = []
    with readonly(config['herbs']) as db:
        latest = db.execute('SELECT dataset_version FROM batches ORDER BY id DESC LIMIT 1').fetchone()
        if not latest: raise ValueError('Herb snapshot is empty')
        version = latest[0]
        rows = db.execute('SELECT * FROM records WHERE dataset_version=? ORDER BY source_record_id',(version,)).fetchall()
        for row in rows:
            path = inside(config['source_root'],row['relative_path'])
            sha = digest(path)
            if sha != row['sha256']:
                raise ValueError('Herb raw file differs from imported snapshot')
            files[row['relative_path']] = sha
            if row['state'] != 'valid': continue
            doc = json.loads(row['document']); raw = read(path)
            if (doc['source_record_id'] != row['source_record_id'] or
                    doc['source']['file_sha256'] != sha or doc['source']['relative_file_path'] != row['relative_path']):
                raise ValueError('Herb document source binding changed')
            reconstructed = parse_herb(path.read_bytes(),row['relative_path'],version)
            if any(doc[k] != reconstructed[k] for k in ('source_record_id','source','fields','raw_basic_section')):
                raise ValueError('Imported herb fields no longer match deterministic source normalization')
            for field in doc['fields'].values():
                s = field.get('source')
                if s and (s['file_sha256'] != sha or pointer(raw,s['json_pointer']) != s['raw_value']):
                    raise ValueError('Structured field provenance differs from raw JSON')
            valid.append((row['source_record_id'],sha))
        sources = [dict(r) for r in db.execute('SELECT dataset_version,count(*) AS count FROM records GROUP BY dataset_version')]
    return {'dataset_version':version,'records':len(rows),'valid_records':len(valid),
            'snapshot_counts':sources,'raw_files_verified':len(files),
            'source_records_sha256':fingerprint(valid),'raw_files_sha256':fingerprint(files)}, files, dict(valid)


def inspect_sidecars(config, herbs, record_hashes, view):
    by_id = {e['id']:e['name_zh_candidate'] for e in view['entries']}
    with readonly(config['enrichment']) as db:
        meta = metadata(db)
        if (meta.get('kind') != 'etcm_enrichment_v1' or meta.get('build_complete') is not True or
                meta.get('dataset_version') != herbs['dataset_version'] or
                meta.get('base_database_sha256') != digest(config['herbs']) or
                not meta.get('source_fingerprint') or
                meta.get('pharma_blocks_sha256') != view['blocks_sha256']):
            raise ValueError('Enrichment is not bound to the registered herb database')
        records = {r['source_record_id']:r['file_sha256'] for r in db.execute('SELECT source_record_id,file_sha256 FROM herbs')}
        if records != record_hashes:
            raise ValueError('Enrichment source rows differ from registered valid herbs')
        problems = []; total = 0
        for row in db.execute('SELECT herb_id,entry_id,name_zh FROM name_candidates ORDER BY herb_id,entry_id'):
            total += 1
            if row['herb_id'] not in records: raise ValueError('Orphan candidate herb')
            if row['entry_id'] not in by_id or row['name_zh'] != by_id[row['entry_id']]:
                problems.append({'source_record_id':row['herb_id'],'entry_id':row['entry_id'],
                    'candidate_name':row['name_zh'],'index_name':by_id.get(row['entry_id']),
                    'reason':'missing_entry' if row['entry_id'] not in by_id else 'candidate_name_changed'})
    with readonly(config['targets']) as db:
        target_meta = metadata(db)
        if (target_meta.get('kind') != 'etcm_target_ingredients_v1' or target_meta.get('build_complete') is not True or
                target_meta.get('dataset_version') != herbs['dataset_version'] or
                target_meta.get('herb_source_fingerprint') != meta.get('source_fingerprint')):
            raise ValueError('Target catalog is not bound to herb sidecar fingerprint')
        target_count = db.execute('SELECT count(*) FROM targets').fetchone()[0]
        ingredient_count = db.execute('SELECT count(*) FROM ingredient_rows').fetchone()[0]
        if target_count != target_meta['targets'] or ingredient_count != target_meta['rows']:
            raise ValueError('Target catalog row count differs from receipt')
    return {'mapping':{'candidate_rows':total,'source_entries_sha256_at_build':meta.get('pharma_entries_sha256'),
                'source_blocks_sha256_at_build':meta.get('pharma_blocks_sha256'),
                'status':'partially_compatible' if problems else 'candidate_ids_and_names_compatible',
                'quarantined_candidates':problems,'identity_verified':False},
            'targets':{'targets':target_count,'ingredient_rows':ingredient_count,
                'binding_verified':True,'scope':'catalog_integrity_and_binding_not_all_relation_source_truth'}}


def inspect_index(index, base, revisions):
    receipt = read(Path(index)/'manifest.json')
    rev = read(Path(revisions)/'summary.json')
    view = read(Path(revisions)/'research_view.json')
    summary = read(Path(base)/'summary.json')
    for name,key in (('research_view','research_view_sha256'),('revision_ledger','ledger_sha256')):
        if digest(Path(revisions)/(name+'.json')) != rev[key]:
            raise ValueError('Revision source changed')
    if (rev['source_entries_sha256'] != summary['entries_sha256'] or
            rev['source_blocks_sha256'] != summary['blocks_sha256'] or
            receipt['view_sha256'] != rev['research_view_sha256'] or
            receipt['index_sha256'] != digest(Path(index)/'research.sqlite3')):
        raise ValueError('Index/revision/raw version chain mismatch')
    entries = {e['id']:e for e in read(Path(base)/'entries.json')['entries']}
    blocks = {b['id']:b for b in read(Path(base)/'blocks.json')}
    revised = {e['id']:e for e in view['entries']}
    if set(entries) != set(revised): raise ValueError('Revision changed source entry set')
    with readonly(Path(index)/'research.sqlite3') as db:
        rows = db.execute('SELECT * FROM chunks').fetchall()
        if db.execute('SELECT count(*) FROM entries').fetchone()[0] != len(entries):
            raise ValueError('Index entry count mismatch')
        if len(rows) != receipt['chunks'] or db.execute('SELECT count(*) FROM fts').fetchone()[0] != len(rows):
            raise ValueError('Index/FTS row counts mismatch')
        for row in rows:
            field = entries[row['entry_id']]['fields'][row['field_index']]
            current = revised[row['entry_id']]['fields'][row['field_index']]
            if (row['scope'] != field['scope'] or row['label'] != field['label'] or
                    row['raw_text'] != field_text(field,blocks) or row['text'] != current['text']):
                raise ValueError('Indexed text differs from registered source field')
            payload = json.loads(row['payload'])
            if payload['evidence'] != current['evidence'] or payload['field_overlay_ids'] != current['overlay_ids']:
                raise ValueError('Indexed evidence/revision receipt mismatch')
    lengths = sorted(len(r['text']) for r in rows)
    return {'entries':len(entries),'chunks':len(rows),'index_sha256':receipt['index_sha256'],
            'view_sha256':receipt['view_sha256'],'fts_rows':len(rows),
            'max_field_chars':max(lengths,default=0),'fields_over_800_chars':sum(n>800 for n in lengths),
            'embedding_truncation_risk':'characters_are_diagnostic_not_token_count; R2 must measure tokenizer truncation',
            'all_index_raw_fields_reconstructed':True,'clinical_or_human_approval':False}, view


def legacy_vectors(config, current_index_sha):
    path = config.get('dense_cache')
    if not path or not (Path(path)/'manifest.json').is_file():
        return {'status':'not_registered','eligible_for_current_release':False}, {}
    path = Path(path); meta = read(path/'manifest.json')
    for name,key in (('vectors.npy','vectors_sha256'),('items.json','items_sha256')):
        if digest(path/name) != meta[key]: raise ValueError('Legacy vector artifact corrupted')
    compatible = meta['index_sha256'] == current_index_sha
    return {'status':'source_index_matches' if compatible else 'incompatible_source_index',
            'eligible_for_current_release':False,'index_sha256':meta['index_sha256'],
            'count':meta['count'],'dimension':meta['dimension'],'model_fingerprint':meta['model_fingerprint'],
            'recipe':meta['recipe'],'truncated_documents':meta.get('truncated_documents'),
            'note':'No encoder or Qdrant loaded; R2 must build/validate a new compatible generation'}, {
                'legacy_dense_'+name.replace('.','_'):path/name for name in ('manifest.json','vectors.npy','items.json')}


def source_files(config):
    result = {role:Path(config[role]) for role in ('herbs','enrichment','targets','pdf_high','pdf_full','seeds')}
    result.update({f'raw_{n}':Path(config['base'])/(n+'.json') for n in ('entries','blocks','summary')})
    result.update({f'revision_{n}':Path(config['revisions'])/(n+'.json')
                   for n in ('summary','research_view','revision_ledger')})
    result.update({'registered_index':Path(config['index'])/'research.sqlite3',
                   'registered_index_manifest':Path(config['index'])/'manifest.json'})
    return result


def build(config, output, *, rebuild_index=True):
    if set(config)-REQUIRED-{'dense_cache','references'} or not REQUIRED.issubset(config):
        raise ValueError('Explicit allowlisted source configuration is required')
    config = {k:str(Path(v).resolve()) for k,v in config.items() if v}
    output = Path(output).resolve()
    # Publishing under a source path could mutate an audited input tree.
    for key in ('base','revisions','index','source_root','dense_cache'):
        if key in config and output.is_relative_to(Path(config[key])):
            raise ValueError('Release must be outside source directories')
    if output.exists(): raise FileExistsError('Never overwrite or replay a data release')
    tracked = source_files(config)
    before = {role:digest(p) for role,p in tracked.items()}
    corpus = RawCorpus(config['base'],config['source_root'],{'high':config['pdf_high'],'full':config['pdf_full']})
    index_stats,view = inspect_index(config['index'],config['base'],config['revisions'])
    herbs,raw_files,record_hashes = inspect_herbs(config)
    sidecars = inspect_sidecars(config,herbs,record_hashes,view)
    vectors,vector_files = legacy_vectors(config,index_stats['index_sha256'])
    tracked.update(vector_files)
    before.update({role:digest(p) for role,p in vector_files.items()})
    seeds = read(config['seeds'])
    reference = freeze(seeds,corpus)
    if config.get('references'):
        if read(config['references']) != reference:
            raise ValueError('Frozen benchmark differs from reviewed source assignments')
        tracked['frozen_reference'] = Path(config['references'])
        before['frozen_reference'] = digest(config['references'])
    # Benchmark bridge records must not rely on a quarantined name mapping.
    quarantine = {x['source_record_id'] for x in sidecars['mapping']['quarantined_candidates']}
    if any(a.get('source_record_id') in quarantine for c in reference['cases'] for a in c['relevant_anchors']):
        raise ValueError('Benchmark selected a quarantined cross-source mapping')
    for case in reference['cases']:
        docs = {a['entry_id'] for a in case['title_anchors']}
        for anchor in case['relevant_anchors']:
            if anchor['kind'] != 'etcm_json': continue
            if (record_hashes.get(anchor['source_record_id']) != anchor['file_sha256'] or
                    raw_files.get(anchor['relative_file_path']) != anchor['file_sha256']):
                raise ValueError('Benchmark JSON source absent from registered herb snapshot')
            if case.get('category') == 'structured_document_bridge':
                with readonly(config['enrichment']) as db:
                    linked = {r[0] for r in db.execute('SELECT entry_id FROM name_candidates WHERE herb_id=?',
                                                       (anchor['source_record_id'],))}
                if not linked & docs:
                    raise ValueError('Benchmark bridge lacks a source-declared name candidate')
    output.mkdir(parents=True,mode=0o700,exist_ok=False)
    write(output/'build_started.json',{'status':'started_not_a_ready_marker','new_model_calls':0})
    try:
        if rebuild_index:
            from scripts.build_mineru_retrieval import build as build_index
            build_index(Path(config['revisions']),output/'index')
            active_index = output/'index'
        else:
            active_index = Path(config['index'])
        rebuilt_stats,_ = inspect_index(active_index,config['base'],config['revisions'])
        candidate_rows = candidates(corpus)
        write(output/'benchmark_candidates.json',{'kind':'unreviewed_candidates_not_gold','cases':candidate_rows})
        write(output/'benchmark_references.json',reference)
        write(output/'benchmark_source_check.json',check_references(reference,corpus))
        write(output/'index_anchor_mapping.json',index_mapping(reference,active_index))
        write(output/'raw_herb_files.json',raw_files)
        registry = {'kind':'local_readonly_references_not_a_portable_backup', 'config':config,
                    'sources':{role:{'path':str(p.resolve()),'sha256':before[role]} for role,p in tracked.items()},
                    'active_index':str(active_index),'raw_herb_files_sha256':digest(output/'raw_herb_files.json')}
        write(output/'sources.private.json',registry);os.chmod(output/'sources.private.json',0o600)
        if any(digest(p) != before[role] for role,p in tracked.items()):
            raise ValueError('Source changed during release build')
        for relative,sha in raw_files.items():
            if digest(inside(config['source_root'],relative)) != sha:
                raise ValueError('Raw herb source changed during build')
        for p in tracked.values():
            if p.suffix == '.sqlite3':
                with readonly(p): pass
        artifacts = {str(p.relative_to(output)):digest(p) for p in output.rglob('*')
                     if p.is_file()}
        code = {str(p.relative_to(ROOT)):digest(p) for p in (ROOT/'tcm/data_release.py',ROOT/'tcm/rag_benchmark.py',
                 ROOT/'scripts/build_data_release.py',ROOT/'scripts/build_rag_benchmark.py',
                 ROOT/'scripts/build_mineru_retrieval.py')}
        result = {'version':'tcm-data-release-v1','status':'ready_research_only','release_id':output.name,
            'sources_sha256':before,'artifacts':artifacts,'code_sha256':code,
            'herbs':herbs,'pharmacopoeia':rebuilt_stats,'sidecars':sidecars,'legacy_vectors':vectors,
            'benchmark':{'candidate_count':len(candidate_rows),'frozen_cases':len(reference['cases']),
                'splits':reference['splits'],'counts':reference['reference_counts'],
                'review_status':'ai_raw_source_checked_pending_human_signoff','human_signoff':False,
                'retrieval_or_generation_scores':None,'reserved_status':reference['reserved_status']},
            'index_rebuilt':rebuild_index,'active_service_configuration_changed':False,
            'raw_inputs_modified':False,'model_calls':0,'vector_builds':0,
            'portability':'registered external sources must remain present and unchanged'}
        result['content_id'] = fingerprint(result)
        # This marker is written last. Failed directories remain diagnostic-only.
        write(output/'release.json',result)
        return result
    except Exception as exc:
        write(output/'failed.json',{'status':'failed_not_publishable','error_type':type(exc).__name__})
        raise


def check(output):
    output = Path(output).resolve();release = read(output/'release.json')
    if release['status'] != 'ready_research_only': raise ValueError('Release is not publishable')
    if release['content_id'] != fingerprint({k:v for k,v in release.items() if k != 'content_id'}):
        raise ValueError('Release manifest content identity changed')
    for name,sha in release['artifacts'].items():
        if digest(inside(output,name)) != sha: raise ValueError('Release artifact changed')
    registry = read(output/'sources.private.json');config = registry['config']
    for role,source in registry['sources'].items():
        if Path(source['path']).suffix == '.sqlite3':
            with readonly(source['path']): pass
        if digest(source['path']) != source['sha256'] or source['sha256'] != release['sources_sha256'][role]:
            raise ValueError('Registered input changed; build a new release')
    with readonly(Path(registry['active_index'])/'research.sqlite3'): pass
    raw_files = read(output/'raw_herb_files.json')
    for relative,sha in raw_files.items():
        if digest(inside(config['source_root'],relative)) != sha: raise ValueError('Registered raw herb changed')
    corpus = RawCorpus(config['base'],config['source_root'],{'high':config['pdf_high'],'full':config['pdf_full']})
    verified = check_references(read(output/'benchmark_references.json'),corpus)
    return {'status':'passed','release_id':release['release_id'],'content_id':release['content_id'],
            'raw_herb_files_verified':len(raw_files),'reference_check':verified,
            'model_calls':0,'vector_builds':0,'active_service_configuration_changed':False}
