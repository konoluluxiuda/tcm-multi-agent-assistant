"""Preview-first same-environment A/B/C provider protocol; explicit opt-in only."""
import argparse
import asyncio
import importlib.metadata as metadata
import json
import os
from pathlib import Path
from time import monotonic

from scripts.eval_pair_live_synthetic import QUESTION, INSTRUCTION, ObservedSDK
import httpx
from scripts.check_pair_protocol import synthetic_fixture, backend_for, verify_raw_witnesses, assess
from experiments.architecture_candidates.pair_protocol import PairProtocol
from experiments.architecture_candidates.deepagents_pair import run_deepagents_pair
from tcm.research_run_sdk import create_sdk

CANDIDATES = ('A_protocol', 'B_deepagents', 'C_protocol')


def preview():
    return {'version': 'abc-live-synthetic-v3-bounded-batches-local-only', 'mode': 'preview', 'question': QUESTION,
            'candidates': list(CANDIDATES), 'endpoint': 'https://api.deepseek.com/chat/completions',
            'model': 'deepseek-flash', 'max_requests_total': 36, 'max_requests_per_candidate': 12,
            'max_output_tokens_per_request': 650, 'max_request_bytes': 98304,
            'max_seconds_per_candidate': 180, 'max_call_seconds': 30, 'retry': False,
            'data': 'Fixed synthetic question, protocol and native framework prompts, exposed business schemas, current synthetic interactions',
            'excluded': ['real pharmacopoeia', 'real ETCM', 'PDFs', 'user history', 'key text in prompts'],
            'instruction': INSTRUCTION, 'parallel_tool_calls': False,
            'admission': 'At most four independent reads or two host-bound delegates; no mixed/dependent batches',
            'live_model_calls': 0}


class SingleActionSDK(ObservedSDK):
    def bind_tools(self, schemas):
        return SingleActionSDK(self.sdk.bind_tools(schemas, parallel_tool_calls=False), self.observations)


class BudgetTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner):
        self.inner = inner
        self.calls = self.candidate_calls = 0
        self.sizes = []

    async def handle_async_request(self, request):
        if str(request.url) != 'https://api.deepseek.com/chat/completions' or request.method != 'POST':
            raise ValueError('Unexpected destination')
        raw = await request.aread()
        body = json.loads(raw)
        limits = [body[k] for k in ('max_tokens', 'max_completion_tokens') if k in body]
        if (len(raw) > 98304 or body.get('model') != 'deepseek-flash' or limits != [650] or
                body.get('stream', False) or body.get('parallel_tool_calls') is not False):
            raise ValueError('Request exceeds reviewed scope')
        if self.calls >= 36 or self.candidate_calls >= 12:
            raise ValueError('HTTP budget exhausted')
        self.calls += 1
        self.candidate_calls += 1
        self.sizes.append(len(raw))
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()


async def evaluate(api_key, *, transport=None):
    mocked = transport is not None
    guard = BudgetTransport(transport if mocked else httpx.AsyncHTTPTransport(retries=0))
    results = []
    with synthetic_fixture() as fixture:
        async with httpx.AsyncClient(transport=guard, follow_redirects=False, trust_env=False) as client:
            for candidate in CANDIDATES:
                guard.candidate_calls = 0
                observations = []
                sdk = SingleActionSDK(create_sdk(model='deepseek-flash', api_key=api_key,
                    base_url='https://api.deepseek.com', http_client=client, call_seconds=30,
                    max_output_tokens=650), observations)
                protocol = PairProtocol(('Alpha', 'Beta'), QUESTION, backend_for(fixture),
                                        model_limit=12, tool_limit=8, seconds=180, call_seconds=30)
                started = monotonic()
                try:
                    result = (await run_deepagents_pair(protocol, sdk) if candidate == 'B_deepagents' else
                              await protocol.run(candidate, sdk, {'Alpha': sdk, 'Beta': sdk}))
                except Exception as error:
                    results.append({'candidate': candidate, 'status': 'provider_error',
                                    'error_type': type(error).__name__, 'http_requests': guard.candidate_calls})
                    break
                result['model_steps'] = result.pop('model_substitute_calls')
                result.update(live_model_calls=0 if mocked else guard.candidate_calls,
                    http_requests=guard.candidate_calls, seconds=round(monotonic()-started, 3), cost=None,
                    model_observations=observations, independent_assessment=assess(result, {'all_targets': True}),
                    verified_target_witness_rows=verify_raw_witnesses(fixture.root, result))
                results.append(result)
                # A failed provider request is not permission to spend the remaining budget.
                if len(observations) != guard.candidate_calls:
                    break
    return {'mode': 'mock_http' if mocked else 'live_synthetic', 'preview': preview(),
            'versions': {n: metadata.version(n) for n in ('langgraph', 'langchain-core', 'langchain-openai',
                'openai', 'pydantic', 'tenacity', 'deepagents')},
            'results': results, 'http_requests': guard.calls, 'request_bytes': guard.sizes,
            'live_model_calls': 0 if mocked else guard.calls,
            'passed': sum(r.get('independent_assessment', {}).get('business_complete', False)
                          and r['status'] == 'complete' for r in results)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not args.live:
        print(json.dumps(preview(), ensure_ascii=False, indent=2))
        return
    if not args.output or args.output.exists():
        parser.error('A new output path is required')
    from dotenv import dotenv_values
    config = {**dotenv_values(Path(__file__).resolve().parents[1]/'.env'), **os.environ}
    if (config.get('CHAT_MODEL') != 'deepseek-flash' or
            config.get('CHAT_BASE_URL', '').rstrip('/') != 'https://api.deepseek.com' or not config.get('CHAT_API_KEY')):
        parser.error('Configured model, endpoint or key does not match the reviewed preview')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as output:
        report = asyncio.run(evaluate(config['CHAT_API_KEY']))
        output.write(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'passed': report['passed'], 'http_requests': report['http_requests'], 'output': str(args.output)}))


if __name__ == '__main__':
    main()
