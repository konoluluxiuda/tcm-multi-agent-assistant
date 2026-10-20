"""Compare current rule orchestration with saved model runs, using fixtures only.

This does not compare production retrieval quality or score semantic correctness.
No model/provider calls are made; only previously saved synthetic runs are read.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
from unittest.mock import patch

from tcm.assistant_fixtures import local, literature
from tcm.assistant_router import resolve
from tcm.mineru_chat import ResearchTool

ROOT = Path(__file__).resolve().parents[1]


class FixtureService:
    receipt = {'index_sha256': 'synthetic-index', 'view_sha256': 'synthetic-view'}

    def check(self):
        pass

    def search(self, question, **kwargs):
        return local(question)


def rule_baseline(question, *, literature_failure=False):
    """Use the existing router and ResearchTool, substituting retrieval data only."""
    started = time.monotonic()
    sources = []
    # Disallow accidental corpus access even if a new route starts recognizing names.
    with patch.dict(os.environ, {'ENABLE_NATURAL_ROUTING': '1'}), patch(
        'tcm.assistant_router.runtime.get_service',
        side_effect=AssertionError('Real corpus access forbidden in synthetic comparison'),
    ):
        selected = resolve(question)
    status = 'unhandled'
    message = '当前规则入口未匹配该问题。'
    if selected and selected['tool'] == 'research':
        result, _ = ResearchTool(FixtureService()).execute(selected['request']['question'], None)
        status, message = result['status'], result['message']
        sources = [{'source_group': 'traditional', 'chunk_id': h['chunk_id'], 'text': h['text']}
                   for h in (result.get('tool_result') or {}).get('hits', [])]
    elif selected and selected['tool'] == 'pubmed':
        if selected['request']['action'] == 'search':
            result = {'status': 'unavailable', 'articles': []} if literature_failure else literature(selected['request']['query'])
            status = result['status']
            message = '只返回合成文献卡片，不生成结论。'
            sources = [{'source_group': 'literature', 'pmid': a['pmid']} for a in result['articles']]
        else:
            status = 'clarification_needed'
            message = '需要检索词或当前会话中的检索批次。'
    elif selected and selected['tool'] == 'assistant':
        status = selected['request']['topic']
        message = '规则入口要求明确来源或检索词；不自动分解跨来源任务。'
    return {'status': status, 'route': selected, 'message': message, 'sources': sources,
            'model_calls': 0, 'duration_ms': round((time.monotonic() - started) * 1000, 3),
            'timing_scope': 'router_and_in_memory_fixture_adapter_only', 'model_api_cost': 0}


def compare(saved):
    if saved.get('data_mode') != 'synthetic_only':
        raise ValueError('Only explicitly synthetic runs can be compared')
    from scripts.eval_evidence_synthetic import CASES
    specifications = {c['id']: c for c in CASES}
    rows = []
    seen = set()
    for row in saved['cases']:
        case = specifications.get(row['id'])
        result = row['result']
        if case is None or row['id'] in seen or row['question'] != case['question']:
            raise ValueError('Unknown, duplicate or changed development question')
        if result.get('data_mode') != 'synthetic_only':
            raise ValueError('Mixed data modes are not permitted')
        seen.add(row['id'])
        baseline = rule_baseline(case['question'], literature_failure=case.get('simulate_literature_failure', False))
        def coverage(sources):
            groups = {s['source_group'] for s in sources}
            chunks = {s.get('chunk_id') for s in sources}
            return {'required_groups_available': set(case['groups']) <= groups if case['groups'] else None,
                    'required_chunks_available': set(case['chunks']) <= chunks if case.get('chunks') else None,
                    'groups': sorted(groups)}
        rows.append({'id': case['id'], 'question': case['question'], 'rule': baseline,
                     'saved_model': {k: result.get(k) for k in ('status', 'model_calls', 'duration_ms', 'input_tokens', 'output_tokens', 'cost')},
                     'rule_source_coverage': coverage(baseline['sources']),
                     'model_source_coverage': coverage(result.get('evidence') or []),
                     'human_task_completion': None, 'human_citation_support': None})
    if seen != set(specifications):
        raise ValueError('The saved development run must contain all five cases')
    return {'data_mode': 'synthetic_only', 'split': 'existing_designed_development',
            'method': 'current deterministic router + ResearchTool + fixture retrieval versus saved live-model fixture run',
            'baseline_limits': 'Fixture retrieval is substituted; this is not full HTTP/UI or production name_field/BM25/dense evaluation. Safe clarification is not scored as an incorrect answer.',
            'timing_limits': 'Rule time is in-process fixture orchestration; model time includes provider calls. These are descriptive single-run timings, not production speedup measurements.',
            'current_source_sha256': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in (
                'tcm/assistant_router.py', 'tcm/mineru_chat.py', 'tcm/assistant_fixtures.py')},
            'new_model_calls': 0, 'business_accuracy': None, 'cases': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--saved-run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    saved_bytes = args.saved_run.read_bytes()
    result = compare(json.loads(saved_bytes))
    result['saved_run_sha256'] = hashlib.sha256(saved_bytes).hexdigest()
    args.out.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
    print(json.dumps({'cases': len(result['cases']), 'new_model_calls': 0, 'business_accuracy': None}))


if __name__ == '__main__':
    main()
