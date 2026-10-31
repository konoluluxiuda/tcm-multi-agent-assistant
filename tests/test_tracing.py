import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from tcm import tracing


class Span:
    def __init__(self, record, fail=False): self.record, self.fail = record, fail
    def update(self, **values):
        if self.fail: raise RuntimeError('export failure')
        self.record.setdefault('updates', []).append(values)
    def score(self, **values):
        if self.fail: raise RuntimeError('score failure')
        self.record.setdefault('scores', []).append(values)


class Client:
    def __init__(self, fail=None): self.records, self.fail = [], fail
    def start_as_current_observation(self, **values):
        if self.fail == 'start': raise RuntimeError('offline')
        self.records.append(values); outer = self
        class Scope:
            def __enter__(self): return Span(values, outer.fail == 'update')
            def __exit__(self, *args):
                values['closed'] = True
                if outer.fail == 'end': raise RuntimeError('offline')
        return Scope()


class TracingTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'ENABLE_LANGFUSE': '1', 'LANGFUSE_TRACING_ENABLED': 'true',
                'TCM_TRACE_CONTENT': '1', 'LANGFUSE_BASE_URL': 'http://localhost:3037',
                'LANGFUSE_PUBLIC_KEY': 'pk-lf-test-only', 'LANGFUSE_SECRET_KEY': 'sk-lf-test-only'})
        self.env.start(); self.addCleanup(self.env.stop)

    def test_disabled_never_initializes_sdk(self):
        with patch.dict(os.environ, {'ENABLE_LANGFUSE': '0'}), patch.object(tracing, '_create_client') as init:
            with tracing.observation('test') as span: span.update(output='okay')
            tracing.flush(); init.assert_not_called()

    def test_content_switch_and_secret_redaction(self):
        fake = Client()
        with patch.object(tracing, 'client', return_value=fake):
            with tracing.observation('safe', input={'api_key': 'whatever', 'text': 'sk-lf-test-only',
                    'reasoning_content': 'private thoughts', 'usage': {'input_tokens': 5}}) as span:
                span.update(output={'authorization': 'Bearer abc'})
            with patch.dict(os.environ, {'TCM_TRACE_CONTENT': '0'}):
                with tracing.observation('metadata', input='DO_NOT_SEND') as span:
                    span.update(output='DO_NOT_SEND', metadata={'status': 'ok'})
        encoded = json.dumps(fake.records)
        for value in ('whatever', 'sk-lf-test-only', 'private thoughts', 'DO_NOT_SEND', 'Bearer abc'):
            self.assertNotIn(value, encoded)
        self.assertEqual(fake.records[0]['input']['usage']['input_tokens'], 5)

    def test_bounded_and_unsupported_payload(self):
        output = tracing.sanitize({'rows': ['x' * 9000 for _ in range(100)], 'object': object()})
        self.assertLess(len(json.dumps(output)), 52000)
        self.assertEqual(tracing.sanitize(object()), '[unsupported]')

    def test_export_failures_do_not_repeat_business_operation(self):
        for fail in ('start', 'update', 'end'):
            count = []
            with patch.object(tracing, 'client', return_value=Client(fail)):
                with tracing.observation('task') as span:
                    count.append(1); span.update(output='done')
            self.assertEqual(count, [1])

    def test_business_error_propagates_without_exporting_error_text(self):
        for fail in (None, 'update', 'end'):
            fake = Client(fail)
            with patch.object(tracing, 'client', return_value=fake):
                with self.assertRaisesRegex(ValueError, 'sensitive original'):
                    with tracing.observation('task'):
                        raise ValueError('sensitive original')
            self.assertNotIn('sensitive original', json.dumps(fake.records))

    def test_interrupt_not_swallowed(self):
        class Interrupt(BaseException): pass
        with patch.object(tracing, 'client', return_value=Client('end')):
            with self.assertRaises(Interrupt):
                with tracing.observation('wait'): raise Interrupt()

    def test_fixed_and_synthetic_graph_keep_distinct_statistics(self):
        from tests.dataops_fixture import make_sources
        from tcm.dataops.baseline import execute
        from tcm.dataops.contracts import Goal
        from tcm.dataops.graph import Engine
        from tcm.dataops.model import SyntheticDriver
        from tcm.dataops.workspace import Workspaces
        fake = Client()
        with tempfile.TemporaryDirectory() as tmp, patch.object(tracing, 'client', return_value=fake):
            root = Path(tmp); _, sources = make_sources(root)
            store = Workspaces(root/'work'); goal = Goal(query={'names': ['Alpha']})
            with sources() as src:
                fixed, _ = execute(store, 'owner', 'fixed-key', src, goal)
                doc, _ = store.reserve_agent('owner', 'agent-key', goal.model_dump(), src.revision, question='合成任务')
            result = Engine(store, 'owner', doc['id'], sources, SyntheticDriver()).run()
        self.assertEqual(fixed['status'], 'complete'); self.assertEqual(result['status'], 'complete')
        self.assertGreater(result['progress']['model_calls'], 0)
        self.assertTrue(all(r['as_type'] == 'span' for r in fake.records))
        root = next(r for r in fake.records if r['name'] == 'dataops.execution')
        self.assertEqual(root['metadata']['provider_requests'], 0)
        self.assertEqual(root['metadata']['workspace_id'], doc['id'])

    def test_native_sdk_one_generation_per_dispatch_not_per_tool_node(self):
        from scripts.eval_dataops_live import configured_model
        from tests.test_dataops_native_loop import ProviderSimulator
        from tcm.dataops.model import OpenAIToolDriver
        provider = ProviderSimulator(); fake = Client()
        with httpx.Client(transport=httpx.MockTransport(provider)) as http:
            driver = OpenAIToolDriver(configured_model('test-only', http), authorized=True)
            context = dict(selected=None)
            with patch.object(tracing, 'client', return_value=fake): result = driver.decide(context)
        self.assertEqual(result['tool'], 'query_records')
        self.assertEqual(len(provider.contexts), 1); self.assertEqual(len(fake.records), 1)
        self.assertEqual(fake.records[0]['as_type'], 'generation')
        usage = next(u['usage_details'] for u in fake.records[0]['updates'] if 'usage_details' in u)
        self.assertEqual(usage['total_tokens'], 15)
        self.assertNotIn(ProviderSimulator.marker, json.dumps(fake.records))

    def test_score_is_separate_from_evaluation_execution(self):
        for fail in (None, 'update'):
            fake = Client(fail)
            with patch.object(tracing, 'client', return_value=fake):
                with tracing.observation('evaluation') as span:
                    span.score('development_contract_pass', 1)
            if not fail:
                self.assertEqual(fake.records[0]['scores'][0]['value'], 1)

    def test_initialization_failure_is_cached_and_does_not_export(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import Mock
        factory = Mock(side_effect=RuntimeError('credential may be here'))
        tracing._create_client.cache_clear()
        self.addCleanup(tracing._create_client.cache_clear)
        with patch.dict(sys.modules, {'langfuse': SimpleNamespace(Langfuse=factory)}):
            with tracing.observation('first'): pass
            with tracing.observation('second'): pass
        self.assertEqual(factory.call_count, 1)


if __name__ == '__main__': unittest.main()
