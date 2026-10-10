"""Compare simple RAG and the existing agent; run modes in separate processes.

No ingestion or collection mutation. Local Qdrant still takes an exclusive lock:
stop other readers/writers of the same directory before running.
"""
import argparse
import hashlib
import importlib.util
import json
import statistics
import time
import uuid
from pathlib import Path


def load_dataset(path):
    raw = path.read_bytes()
    rows = json.loads(raw)
    if not isinstance(rows, list) or not rows:
        raise ValueError('Dataset must be a nonempty JSON array')
    ids = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Each case must be an object')
        for key in ('id', 'question', 'reference_answer', 'source'):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError(f'Each case needs a nonempty string: {key}')
        if row['id'] in ids:
            raise ValueError(f'Duplicate case ID: {row["id"]}')
        ids.add(row['id'])
    return rows, hashlib.sha256(raw).hexdigest()


def source_name(value):
    return str(value or '').replace('\\', '/').rsplit('/', 1)[-1]


def summarize(cases):
    successful = [c for c in cases if c['status'] == 'ok']
    retrieved = [c for c in cases if c['retrieval_completed']]
    return {
        'cases': len(cases), 'successful': len(successful),
        'errors': sum(c['status'] == 'error' for c in cases),
        'retrieval_completed': len(retrieved),
        'source_file_hit_rate_all': sum(c['source_file_hit'] for c in cases) / len(cases) if cases else None,
        'source_file_hit_rate_retrieved': sum(c['source_file_hit'] for c in retrieved) / len(retrieved) if retrieved else None,
        'successful_latency_median_s': statistics.median(c['latency_s'] for c in successful) if successful else None,
        'chat_calls_started': sum(c['chat_calls_started'] for c in cases),
        'warning': 'File hits and successful execution do not measure answer correctness. No clinical/Chinese quality conclusion follows.',
    }


def run_case(row, mode, run, trace, counter):
    trace.clear()
    counter.reset()
    start = time.monotonic()
    record = {'id': row['id'], 'question': row['question'],
              'reference_answer': row['reference_answer'], 'expected_source': row['source'],
              'answer': '', 'agent': None, 'status': 'ok', 'error': None}
    try:
        result = run(row['question'])
        record.update(answer=result['answer'], agent=result['agent'])
        if result.get('error'):
            raise RuntimeError(result['error'])
    except Exception as exc:
        record.update(status='error', error=f'{type(exc).__name__}: {exc}')
    # Preserve retrieval and RAG output even when a later step fails.
    record.update(mode=mode, latency_s=round(time.monotonic() - start, 3),
                  retrieval_completed='retrieved' in trace,
                  retrieved=trace.get('retrieved', []), rag_result=trace.get('rag_result'),
                  chat_calls_started=counter.started, chat_calls_completed=counter.completed,
                  reported_token_usage=counter.usage)
    record['source_file_hit'] = any(source_name(d.get('source')) == source_name(row['source'])
                                    for d in record['retrieved'])
    # Human review slots: intentionally no English keyword-overlap accuracy proxy.
    record['review'] = {'answer_supported': None, 'citation_correct': None,
                        'appropriate_abstention': None, 'notes': ''}
    return record


def build_runner(mode, trace):
    from config import Config
    from langchain_core.callbacks import BaseCallbackHandler
    from agents.rag_agent.vectorstore_qdrant import VectorStore
    from agents.rag_agent.response_generator import ResponseGenerator

    class CallCounter(BaseCallbackHandler):
        def reset(self):
            self.started, self.completed, self.usage = 0, 0, []
        def on_chat_model_start(self, *args, **kwargs):
            self.started += 1
        def on_llm_end(self, response, **kwargs):
            self.completed += 1
            usage = (response.llm_output or {}).get('token_usage')
            if usage:
                self.usage.append(usage)

    counter = CallCounter()
    counter.reset()
    config = Config()
    resources = []
    restore = []

    def instrument(cfg):
        seen = set()
        for section in vars(cfg).values():
            for model in vars(section).values() if hasattr(section, '__dict__') else []:
                if hasattr(model, 'callbacks') and hasattr(model, 'invoke') and id(model) not in seen:
                    seen.add(id(model))
                    old = model.callbacks
                    model.callbacks = [counter]
                    restore.append(lambda m=model, old=old: setattr(m, 'callbacks', old))

    def capture(store):
        original = store.retrieve_relevant_chunks
        def retrieve(*args, **kwargs):
            docs = original(*args, **kwargs)
            trace['retrieved'] = [dict(d) for d in docs]
            return docs
        store.retrieve_relevant_chunks = retrieve

    def close():
        for undo in reversed(restore):
            undo()
        for store in resources:
            store.client.close()

    if mode == 'simple':
        instrument(config)
        store = VectorStore(config)
        resources.append(store)
        capture(store)
        vectorstore, docstore = store.load_vectorstore()
        generator = ResponseGenerator(config)
        def run(query):
            docs = store.retrieve_relevant_chunks(query, vectorstore, docstore)
            result = generator.generate_response(query, docs, [])
            trace['rag_result'] = result
            # Existing generator swallows provider exceptions into this sentinel.
            error = 'ResponseGenerator returned its error fallback' if result['response'].startswith('I apologize, but I encountered an error') else None
            return {'answer': result['response'], 'agent': 'SIMPLE_RAG', 'error': error}
    else:
        import agents.agent_decision as ad
        from agents.rag_agent import MedicalRAG
        config = ad.config
        instrument(config)
        original_factory = ad.MedicalRAG
        restore.append(lambda: setattr(ad, 'MedicalRAG', original_factory))
        # Reuse components to avoid repeatedly opening the local Qdrant directory.
        # This benchmark-only lifecycle fix is disclosed in the report metadata.
        shared = []
        def factory(cfg):
            if not shared:
                rag = MedicalRAG(cfg)
                resources.append(rag.vector_store)
                capture(rag.vector_store)
                original = rag.process_query
                def process(*args, **kwargs):
                    result = original(*args, **kwargs)
                    trace['rag_result'] = result
                    return result
                rag.process_query = process
                shared.append(rag)
            return shared[0]
        ad.MedicalRAG = factory
        def run(query):
            state = ad.process_query(query, session_id=f"benchmark-{uuid.uuid4()}")
            answer = state['messages'][-1].content
            rag_result = trace.get('rag_result') or {}
            error = 'RAG returned an internal error fallback' if str(rag_result.get('response', '')).startswith(('I encountered an error', 'I apologize, but I encountered an error')) else None
            return {'answer': answer, 'agent': state.get('agent_name'), 'error': error}
    return run, close, counter, config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=Path('data/eval/qa_dataset.json'))
    parser.add_argument('--mode', choices=['simple', 'full'], default='simple')
    parser.add_argument('--check', action='store_true', help='Validate dataset and list dependency availability without model calls')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--out', type=Path, default=Path('data/eval/comparison'))
    args = parser.parse_args()
    rows, digest = load_dataset(args.dataset)
    if args.limit is not None:
        if args.limit < 1:
            parser.error('--limit must be positive')
        rows = rows[:args.limit]
    if args.check:
        print(json.dumps({'dataset_sha256': digest, 'cases': len(rows),
                          'dependencies': {name: importlib.util.find_spec(name) is not None for name in
                                           ['langchain_core', 'langchain_openai', 'qdrant_client', 'docling', 'torch']},
                          'live_validation': False}, indent=2))
        return
    trace = {}
    run, close, counter, config = build_runner(args.mode, trace)
    args.out.mkdir(parents=True, exist_ok=True)
    run_id = f'{time.time_ns()}-{args.mode}'
    path = args.out / f'{run_id}.jsonl'
    cases = []
    try:
        with path.open('x', encoding='utf-8') as stream:
            for row in rows:
                rec = run_case(row, args.mode, run, trace, counter)
                stream.write(json.dumps(rec, ensure_ascii=False, default=str) + '\n')
                stream.flush()
                cases.append(rec)
                print(f'{rec["id"]}: {rec["status"]}, {rec["latency_s"]}s', flush=True)
    finally:
        close()
    report = {'mode': args.mode, 'dataset_sha256': digest, 'case_ids': [r['id'] for r in rows],
              'chat_model': config.rag.llm.model_name, 'embedding_model': config.rag.embedding_model.model,
              'embedding_dim': config.rag.embedding_dim,
              'method': 'Same corpus and hybrid retrieval; simple skips expansion, reranking, routing and guardrails. Full uses isolated sessions and reused RAG components. First-use costs may differ; no cost estimate without provider pricing. Counts exclude embeddings and provider retries.',
              'summary': summarize(cases), 'cases_file': str(path)}
    path.with_suffix('.summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
