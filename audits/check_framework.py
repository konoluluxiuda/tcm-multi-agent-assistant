"""Offline probes of real repository methods; no model calls or DB writes.

AST loading skips dependency imports and constructors. Injected model outputs
exercise deterministic contracts, NOT real model quality or integration.
Run from the repository root: python audits/check_framework.py
"""
import ast
import json
import logging
import re
import typing
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agents.source_links import source_url
results = []


def load_nodes(path, names, extra=None):
    tree = ast.parse((ROOT / path).read_text())
    selected = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name in names]
    assert len(selected) == len(names), (path, names)
    namespace = {**vars(typing), 're': re, 'logging': logging, 'Path': Path, 'source_url': source_url}
    namespace.update(extra or {})
    exec(compile(ast.Module(body=selected, type_ignores=[]), path, 'exec'), namespace)
    return namespace


def record(name, observed, issue, detail):
    results.append(dict(name=name, status='ISSUE_REPRODUCED' if issue else 'CHECK_PASSED',
                        observed=observed, detail=detail))


ns = load_nodes('agents/rag_agent/content_processor.py', ['ContentProcessor'])
processor = ns['ContentProcessor'].__new__(ns['ContentProcessor'])
processor.chunker_model = SimpleNamespace(invoke=lambda _: SimpleNamespace(content='split_after: 0'))
long_text = 'herbal evidence ' * 1200
chunks = processor.chunk_document(long_text)
record('long_section_not_bounded', [len(c.split()) for c in chunks],
       any(len(c.split()) > 512 for c in chunks),
       'A valid split response cannot split a single long heading section internally.')
marked = '<|start_chunk_0|>alpha<|end_chunk_0|>\n<|start_chunk_1|>beta<|end_chunk_1|>'
chunks = processor._split_text_by_llm_suggestions(marked, '0, 1')
record('bare_split_ids', chunks, any('<|start_chunk_' in c for c in chunks),
       'Prompt also asks for IDs only; parser needs the split_after: prefix.')
chunks = processor._split_text_by_llm_suggestions(marked, 'split_after: 0, 1')
record('valid_split_preserves_text', chunks, chunks != ['alpha', 'beta'],
       'Positive control: expected format yields both original sections.')

ns = load_nodes('agents/rag_agent/response_generator.py', ['ResponseGenerator'])
generator = ns['ResponseGenerator'].__new__(ns['ResponseGenerator'])
scores = [generator._calculate_confidence([{'combined_score': x}]) for x in [-3.0, 4.0]]
record('confidence_not_probability', scores, any(not 0 <= x <= 1 for x in scores),
       'Injected scores demonstrate no normalization despite the documented 0..1 contract.')
sources = generator._extract_sources([{'source': 'reference.pdf', 'source_path': '/data/reference.pdf',
                                      'page': 12, 'doc_id': 'chunk-1', 'score': 0.7}])
record('citation_loses_locator', sources, 'page' not in sources[0] and 'doc_id' not in sources[0],
       'Even supplied page/chunk identifiers are absent from the returned citation.')

ns = load_nodes('agents/rag_agent/reranker.py', ['Reranker'])
ranker = ns['Reranker'].__new__(ns['Reranker'])
ranker.logger = logging.getLogger('offline-audit')
def unavailable(_):
    raise RuntimeError('injected reranker failure')
ranker.model = SimpleNamespace(predict=unavailable)
docs = [{'id': 'a', 'content': 'a', 'score': 0.5}, {'id': 'b', 'content': 'b', 'score': 0.4}]
fallback = ranker.rerank('query', docs, '/unused')
record('reranker_fallback_contract', {'type': type(fallback).__name__, 'length': len(fallback)},
       not isinstance(fallback, tuple),
       'Caller expects (documents, picture_paths), but exception fallback returns documents only.')

ns = load_nodes('eval_rag.py', ['content_tokens', 'keyword_recall'], {'STOPWORDS': set()})
recall = ns['keyword_recall']('中草药来源核对', '中草药来源核对')
record('chinese_keyword_metric', recall, recall != 1.0,
       'Identical Chinese reference and answer score zero; metric cannot assess the planned Chinese use case.')

report = json.loads((ROOT / 'data/eval/report.json').read_text())
cases = report['cases']
lost = [c['id'] for c in cases if c.get('error') and c.get('rag_confidence') is None]
record('historical_error_cases_unobservable', lost, bool(lost),
       'These records cannot distinguish direct search routing from RAG followed by failed search; do not infer retrieval was never attempted.')

output = {'scope': 'offline deterministic probes; injected outputs; no live API validation', 'results': results}
print(json.dumps(output, ensure_ascii=False, indent=2))
