"""Local fan-out/fan-in evidence reports; no model generation or network access."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from typing import TypedDict

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ('TCM_DB', 'TCM_DATASET_VERSION', 'ETCM_ENRICHMENT_DB', 'PHARMACOPOEIA_INDEX',
          'LOCAL_DENSE_MODEL', 'LOCAL_DENSE_CACHE', 'LOCAL_DENSE_PYTHON', 'LOCAL_QDRANT_GENERATION', 'LOCAL_RERANKER_MODEL', 'RESEARCH_REPORT_TRANSPORT', 'RESEARCH_MCP_PYTHON')


def fuse(rankings, k=6):
    """Reciprocal rank fusion, not a learned reranker. Stable ties by chunk ID."""
    scores, hits = {}, {}
    for ranking in rankings:
        seen = set()
        for rank, hit in enumerate(ranking, 1):
            key = hit['chunk_id']
            if key in seen:
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
            hits[key] = hit
    return [{**hits[key], 'fusion_score': scores[key]} for key in
            sorted(scores, key=lambda key: (-scores[key], key))[:k]]


def text_search(herb, question):
    from tcm.mineru_retrieval import ResearchRetriever, resolve_names
    with_retriever = ResearchRetriever(os.environ['PHARMACOPOEIA_INDEX'])
    try:
        r = with_retriever
        if herb not in r.names:
            return {'status': 'name_not_resolved', 'hits': []}
        # Explicit entity never permits a second entity to leak into this report.
        names = resolve_names(question, r.names)
        if any(name != herb for name in names):
            return {'status': 'multiple_entities', 'hits': []}
        from tcm.report_request import interpret_report_request, organize_fields
        request = interpret_report_request(herb, question)
        interpreted = request['search_question']
        query = interpreted if names else herb + '的' + interpreted
        gate = r.query(query, method='name_field', k=20)
        if gate['status'] not in ('candidates', 'field_not_resolved'):
            return {**gate, 'retrieval': 'guarded_fields'}
        lexical = r.query(query, method='name_bm25', k=20)
        if lexical['status'] in ('ambiguous_name', 'ambiguous_field', 'reference_unresolved', 'partial_evidence'):
            return {**lexical, 'retrieval': 'guarded_bm25'}
        rankings = [lexical['hits']]
        if gate['status'] == 'candidates':
            rankings.append(gate['hits'])
        if not any(rankings):
            return {**lexical, 'retrieval': {'method':'guarded_lexical','dense':'not_used_without_lexical_evidence','reranker':'none'}}
        metadata = {'method': 'rrf', 'dense': 'not_configured', 'reranker': 'none'}
        if os.getenv('LOCAL_DENSE_MODEL') and os.getenv('LOCAL_DENSE_CACHE'):
            metadata['backend'] = 'qdrant_local' if os.getenv('LOCAL_QDRANT_GENERATION') else 'numpy'
            try:
                from tcm.local_dense import LocalEncoder, DenseRetriever
                encoder = LocalEncoder(Path(os.environ['LOCAL_DENSE_MODEL']), threads=2)
                if os.getenv('LOCAL_QDRANT_GENERATION'):
                    from tcm.qdrant_dense import QdrantDenseRetriever
                    dense = QdrantDenseRetriever(r, encoder, Path(os.environ['LOCAL_DENSE_CACHE']),
                                                 Path(os.environ['LOCAL_QDRANT_GENERATION']))
                    metadata['backend'] = 'qdrant_local'
                else:
                    dense = DenseRetriever(r, encoder, Path(os.environ['LOCAL_DENSE_CACHE']))
                    metadata['backend'] = 'numpy'
                found = dense.query(query, method='name_dense', k=20)
                if found['status'] in ('candidates',):
                    rankings.append(found['hits']); metadata['dense'] = 'used'
                elif found['status'] in ('ambiguous_name', 'ambiguous_field', 'reference_unresolved', 'partial_evidence'):
                    return {**found, 'retrieval': 'guarded_dense'}
                else:
                    metadata['dense'] = found['status']
            except (ImportError, OSError, ValueError, KeyError, RuntimeError):
                metadata['dense'] = 'unavailable'
        candidates = fuse(rankings, k=20)
        from tcm.local_reranker import maybe_rerank
        hits, rerank = maybe_rerank(query, candidates, os.getenv('LOCAL_RERANKER_MODEL'))
        # Recover candidates omitted by the reranker cap before explicit field ordering.
        seen = {h['chunk_id'] for h in hits}
        ordered = hits + [h for h in candidates if h['chunk_id'] not in seen]
        hits, missing = organize_fields(ordered, request)
        metadata['request'] = request
        metadata['missing_requested_fields'] = missing
        metadata['reranker'] = rerank
        # A capped result is a selection, never evidence of exhaustive field coverage.
        return {**gate, 'status': ('partial_evidence' if missing else 'candidates') if hits else 'no_match', 'hits': hits,
                'retrieval': metadata, 'selection_limit': 6, 'exhaustive': False}
    finally:
        with_retriever.close()


def structured_search(herb):
    from tcm.chat import execute_request, FIELD_NAMES
    labels = {value: key for key, value in reversed(list(FIELD_NAMES.items()))}
    result = execute_request({'name': herb})
    records = result['records']
    base = {key: result.get(key) for key in ('status', 'dataset_version', 'total_record_count')}
    if len(records) != 1:
        return {**base, 'rows': [], 'options': [r['source_record_id'] for r in records]}
    record = records[0]
    rows = []
    for key, field in record['fields'].items():
        if field['state'] != 'present' or key not in labels:
            continue
        rows.append({'label': labels[key], 'value': field.get('translation', {}).get('text') or field['value'],
                     'original_value': field['value'], 'source': field['source']})
    return {**base, 'rows': rows, 'source': record['source'], 'source_record_id': record['source_record_id'],
            'identity_verified': False}


async def local_branch(branch, herb, question, timeout=45):
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'LANG', 'SYSTEMROOT', 'TEMP', 'TMP', *CONFIG)}
    env.update(PYTHONPATH=str(ROOT), PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    executable = os.getenv('LOCAL_DENSE_PYTHON') if branch == 'text' else None
    paths = ([Path(env['PHARMACOPOEIA_INDEX']) / 'research.sqlite3',
              Path(env['PHARMACOPOEIA_INDEX']) / 'manifest.json'] if branch == 'text'
             else [Path(env[k]) for k in ('TCM_DB', 'ETCM_ENRICHMENT_DB') if env.get(k)])
    def tokens():
        return [(str(p), p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns) for p in paths]
    before = tokens()
    proc = await asyncio.create_subprocess_exec(executable or sys.executable, '-m', 'tcm.cross_source_report',
        '--worker', stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, cwd=ROOT, env=env)
    try:
        raw, _ = await asyncio.wait_for(proc.communicate(json.dumps(
            {'branch': branch, 'herb': herb, 'question': question}).encode()), timeout)
        if proc.returncode or len(raw) > 400000:
            raise ValueError('Local branch failed')
        if before != tokens():
            return {'status': 'source_changed'}
        return json.loads(raw)
    finally:
        if proc.returncode is None:
            proc.kill(); await proc.wait()


class ReportState(TypedDict, total=False):
    text: dict
    structured: dict
    report: dict


async def run_report(payload, *, on_state=None, backend=None, checkpointer=None, thread_id=None):
    from langgraph.graph import StateGraph, START, END
    from tcm.mcp_client import report_branch
    backend = backend or report_branch
    herb, question = payload['herb'], payload['question']
    async def branch(name):
        started = time.monotonic()
        try:
            data = await backend(name, herb, question)
        except asyncio.TimeoutError:
            data = {'status': 'timeout'}
        except Exception:
            data = {'status': 'unavailable'}
        return {name: {**data, 'duration_ms': round((time.monotonic()-started)*1000)}}
    async def text_node(state): return await branch('text')
    async def structured_node(state): return await branch('structured')
    def assemble(state):
        text, structured = state['text'], state['structured']
        available = bool(text.get('hits')), bool(structured.get('rows'))
        usable_text = text['status'] == 'candidates'
        usable_structure = structured['status'] in ('ok', 'name_mapping_candidates')
        status = 'complete' if all(available) and usable_text and usable_structure else 'partial' if any(available) else 'needs_input'
        return {'report': {'kind': 'cross_source', 'mode': 'local_evidence_graph', 'status': status,
                'herb': herb, 'question': question, 'text': text, 'structured': structured,
                'identity_merged': False, 'answer_generated': False,
                'scope': '药典相关字段与ETCM基础属性并列展示，不代表已回答全部研究问题。'}}
    graph = StateGraph(ReportState)
    graph.add_node('text_research', text_node); graph.add_node('structured_research', structured_node)
    graph.add_node('assemble', assemble)
    graph.add_edge(START, 'text_research'); graph.add_edge(START, 'structured_research')
    graph.add_edge(['text_research', 'structured_research'], 'assemble'); graph.add_edge('assemble', END)
    compiled=graph.compile(checkpointer=checkpointer)
    config={'configurable':{'thread_id':thread_id}} if checkpointer else None
    initial={'text': {}, 'structured': {}}
    if checkpointer:
        if not thread_id:raise ValueError('Checkpoint requires a private thread identifier')
        previous=await compiled.aget_state(config)
        if previous.values:
            initial=None
            if not previous.next and previous.values.get('report'):
                return previous.values['report']
    if on_state is None:
        # LangGraph 0.3 forces values mode for nested graphs; consume its final state.
        final = await compiled.ainvoke(initial,config=config)
        return final['report']
    state = {}; events = []
    async for update in compiled.astream(initial, config=config, stream_mode='updates'):
        for name, values in update.items():
            # Nested graphs may attach __metadata__ to update frames.
            if name not in ('text_research', 'structured_research', 'assemble') or not values:
                continue
            state.update(values)
            events.append({'tool': name, 'status': next(iter(values.values())).get('status', 'complete')})
            if on_state:
                await on_state({'last_node': name, 'tool_calls': sum(x['tool'] != 'assemble' for x in events),
                                'model_calls': 0, 'events': list(events)})
    return state['report']


def worker():
    import socket
    def denied(*args, **kwargs): raise RuntimeError('Network disabled')
    for key in ('connect', 'connect_ex', 'sendto'): setattr(socket.socket, key, denied)
    socket.getaddrinfo = denied
    request = json.loads(sys.stdin.buffer.read(8192))
    if request['branch'] == 'text':
        result = text_search(request['herb'], request['question'])
    elif request['branch'] == 'structured':
        result = structured_search(request['herb'])
    elif request['branch'] == 'shared_targets':
        from tcm.relation_queries import shared_targets
        selection = json.loads(request['question'])
        result = shared_targets(os.environ['ETCM_ENRICHMENT_DB'], selection['records'], selection['version'])
        result.update(status='ok', source_ids=[r['source_record_id'] for r in selection['records']],
                      dataset_version=selection['version'])
    else:
        raise ValueError('Unknown branch')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    if sys.argv[1:] != ['--worker']: raise SystemExit('Internal local worker only')
    worker()
