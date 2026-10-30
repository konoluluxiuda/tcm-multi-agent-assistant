"""Same-environment A/B/C synthetic business comparison; no provider calls."""
import argparse
import asyncio
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import socket
from unittest.mock import patch

for key in ('LANGCHAIN_TRACING', 'LANGCHAIN_TRACING_V2', 'LANGSMITH_TRACING'):
    os.environ[key] = 'false'

from scripts.check_pair_protocol import synthetic_fixture, backend_for, verify_raw_witnesses, assess
from experiments.architecture_candidates.pair_protocol import PairProtocol, drivers
from experiments.architecture_candidates.deepagents_pair import run_deepagents_pair


CASES = [('first_page', False, None), ('all_targets', True, None),
         ('missing_field', True, 'missing_field'), ('unavailable_side', True, 'unavailable_side')]


def input_measurements(models):
    sizes = [len(json.dumps({'messages': messages, 'tools': model.schemas},
                            ensure_ascii=False, default=str).encode())
             for model in models for messages in model.inputs]
    return {'serialization': 'LangChain messages model_dump plus exposed tool schemas, UTF-8',
            'summed_input_bytes': sum(sizes), 'max_input_bytes': max(sizes, default=0),
            'input_bytes_per_call': sizes, 'provider_tokens': None, 'cost': None}


async def check():
    results = []
    with synthetic_fixture() as fixture:
        for case, all_targets, fault in CASES:
            for candidate in ('A_protocol', 'B_deepagents', 'C_protocol'):
                sdk, children = drivers('A_protocol' if candidate == 'B_deepagents' else candidate,
                                        pages=2 if all_targets else 1)
                protocol = PairProtocol(('Alpha', 'Beta'),
                    '比较功能与主治、注意事项，列出'+('全部共同靶点' if all_targets else '共同靶点首页'),
                    backend_for(fixture, fault), all_targets=all_targets, model_limit=12, tool_limit=8)
                if candidate == 'B_deepagents':
                    result = await run_deepagents_pair(protocol, sdk)
                else:
                    result = await protocol.run(candidate, sdk, children)
                result.update(case_id=case, independent_assessment=assess(result, {'all_targets': all_targets}),
                              verified_target_witness_rows=verify_raw_witnesses(fixture.root, result),
                              input_measurements=input_measurements([sdk, *children.values()]))
                expected = 'partial' if fault else 'complete'
                if result['status'] != expected or result['independent_assessment']['business_complete'] != (fault is None):
                    raise AssertionError((case, candidate, result['status'], result['reason']))
                if not fault and len(result['targets']['items']) != (23 if all_targets else 20):
                    raise AssertionError('Target coverage differs')
                results.append(result)
    return {'status': 'passed', 'mode': 'fixed_sdk_synthetic_business_comparison',
            'versions': {n: metadata.version(n) for n in ('langgraph', 'langchain', 'langchain-core',
                'langchain-openai', 'openai', 'pydantic', 'deepagents')},
            'results': results, 'external_model_calls': 0,
            'limitations': ['Fixed SDK decisions do not measure autonomous model planning',
                'No persistence or uncertainty recovery compared for these pair adapters',
                'Serialized input bytes are not provider tokens or cost',
                'C delegation is sequential; no parallel speed measurement',
                'Pydantic/Tenacity differ from production, but all three candidates use the same environment']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    attempts = []
    def deny_network(*args, **kwargs):
        attempts.append('blocked')
        raise RuntimeError('No network permitted in architecture comparison')
    with patch.object(socket.socket, 'connect', deny_network), patch.object(socket.socket, 'connect_ex', deny_network), \
         patch.object(socket.socket, 'sendto', deny_network), patch.object(socket, 'getaddrinfo', deny_network):
        report = asyncio.run(check())
    if attempts:
        raise AssertionError('Unexpected network attempt')
    root = Path(__file__).resolve().parents[1]
    paths = [Path(__file__), root/'scripts/check_pair_protocol.py', root/'scripts/check_pair_architecture_preflight.py',
             root/'experiments/architecture_candidates/pair_protocol.py',
             root/'experiments/architecture_candidates/deepagents_pair.py',
             root/'requirements-architecture-candidates.txt', root/'constraints-architecture-candidates.txt',
             *(root/'tcm').glob('*.py')]
    report['source_sha256'] = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    report['network_attempts'] = len(attempts)
    (args.out/'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'status': report['status'], 'runs': len(report['results']),
                      'complete': sum(r['status'] == 'complete' for r in report['results']),
                      'external_model_calls': 0}))


if __name__ == '__main__':
    main()
