"""SDK/wire/graph contracts with a scripted provider simulator, not LLM quality."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import httpx

from scripts.eval_dataops_live import configured_model, budget_transport
from tests.dataops_fixture import make_sources
from tcm.dataops.contracts import Goal
from tcm.dataops.evaluation import SourceOracle, run_case
from tcm.dataops.graph import Engine, Limits
from tcm.dataops.model import OpenAIToolDriver, SyntheticDriver
from tcm.dataops.workspace import Workspaces, Conflict


class ProviderSimulator:
    """Only test-owned JSON/AI responses; never connect to a provider."""
    marker = 'DO_NOT_SAVE_REJECTED_CONTENT'

    def __init__(self, fault=None, at=2, repeat=False):
        self.fault, self.at, self.repeat = fault, at, repeat
        self.contexts = []

    def __call__(self, request):
        data = json.loads(request.read())
        context = json.loads(data['messages'][-1]['content'])
        self.contexts.append(context)
        action = SyntheticDriver().decide(context)
        call = {'id': 'action-' + str(len(self.contexts)), 'type': 'function',
                'function': {'name': action['tool'], 'arguments': json.dumps(action['arguments'])}}
        calls = [call]
        if self.fault and (self.repeat or len(self.contexts) == self.at):
            if self.fault == 'timeout':
                raise httpx.ReadTimeout(self.marker)
            if self.fault == 'none':
                calls = []
            elif self.fault == 'multiple':
                calls = [call, {**deepcopy(call), 'id': 'second'}]
            elif self.fault == 'json':
                call['function']['arguments'] = self.marker
            elif self.fault == 'arguments':
                call['function']['arguments'] = json.dumps({'purpose': self.marker, 'invented': True})
            elif self.fault == 'unregistered':
                call['function']['name'] = self.marker
        return httpx.Response(200, json={'id': 'offline', 'object': 'chat.completion', 'created': 0,
            'model': 'deepseek-flash', 'choices': [{'index': 0, 'finish_reason': 'tool_calls', 'message': {
                'role': 'assistant', 'content': self.marker, 'tool_calls': calls}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}})


class MeasuredNative:
    label = 'offline_sdk_native_simulator'

    def __init__(self, model, corrections):
        self.max_response_corrections = corrections
        self.inner = OpenAIToolDriver(model, authorized=True, max_response_corrections=corrections)
        self.context_bytes = []

    def decide(self, context):
        self.context_bytes.append(len(json.dumps(context, ensure_ascii=False).encode()))
        return self.inner.decide(context)


class NativeLoopTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        _, self.factory = make_sources(self.root / 'fixture')
        self.store = Workspaces(self.root / 'work')
        self.sequence = 0

    def driver(self, simulator, corrections=1):
        out = self.root / ('receipts-' + str(self.sequence)); self.sequence += 1
        out.mkdir()
        http = httpx.Client(transport=budget_transport(out, inner=httpx.MockTransport(simulator)))
        self.addCleanup(http.close)
        return MeasuredNative(configured_model('offline-key', http), corrections)

    def create(self):
        with self.factory() as source:
            doc, _ = self.store.reserve_agent('owner', 'native-' + str(self.sequence),
                Goal(query={'names': ['Alpha']}, document_fields=['功能与主治', '注意']).model_dump(),
                source.revision, question='核对合成资料')
        return doc

    def engine(self, doc, driver, **kwargs):
        return Engine(self.store, 'owner', doc['id'], self.factory, driver, **kwargs)

    def test_three_pilot_tasks_roundtrip_through_sdk_and_verify_workbooks(self):
        suite = json.loads(Path('eval/dataops_tasks_v1.json').read_text())
        oracle = SourceOracle(self.root / 'fixture')
        for case in (c for c in suite['cases'] if c['case_id'] in ('B01', 'B04', 'R01')):
            with self.subTest(case=case['case_id']):
                simulator = ProviderSimulator()
                driver = self.driver(simulator)
                result = run_case(case, oracle.reference(case), oracle, 'A', self.factory,
                    self.root / case['case_id'], driver_factory=lambda *_: driver)
                self.assertEqual(result['status'], 'passed', result)
                self.assertTrue(any(c['artifact'] and c['verified'] for c in simulator.contexts))
                if case['case_id'] == 'B04':
                    self.assertTrue(any(c['ambiguities'] for c in simulator.contexts))
                if case['case_id'] == 'R01':
                    self.assertTrue(any(c['observation'] and c['observation'].get('reused_cells', 0) > 0
                                        for c in simulator.contexts))

    def test_received_invalid_response_corrects_once_without_executing_its_calls(self):
        for fault in ('none', 'multiple', 'json', 'arguments', 'unregistered'):
            with self.subTest(fault=fault):
                simulator = ProviderSimulator(fault)
                driver = self.driver(simulator); doc = self.create()
                result = self.engine(doc, driver).run()
                self.assertEqual(result['status'], 'complete', result)
                self.assertEqual(result['progress']['response_corrections'], 1)
                self.assertEqual(result['progress']['model_calls'], 7)
                self.assertEqual(result['progress']['tool_calls'], 6)
                self.assertEqual(result['progress']['leaf_reads'], 3)
                feedback = simulator.contexts[2]['observation']
                self.assertEqual((feedback['tool'], feedback['executed_tool_calls']), ('model_response', 0))
                self.assertEqual(sum(u['total_tokens'] for u in result['result']['usage']), 105)
                with sqlite3.connect(self.store.root / doc['id'] / 'dispatch.sqlite3') as db:
                    self.assertNotIn(simulator.marker, json.dumps(db.execute('SELECT * FROM dispatch').fetchall()))
                self.engine(doc, driver).run()
                self.assertEqual(len(simulator.contexts), 7)

    def test_repeated_bad_response_stops_after_one_correction(self):
        simulator = ProviderSimulator('multiple', repeat=True)
        driver = self.driver(simulator); doc = self.create()
        result = self.engine(doc, driver).run()
        self.assertEqual((result['status'], result['error']), ('partial', 'model_response_rejected'))
        self.assertEqual(result['progress']['model_calls'], 2)
        self.assertEqual(result['progress']['tool_calls'], 0)
        self.assertIsNone(result['artifact'])
        self.engine(doc, driver).run()
        self.assertEqual(len(simulator.contexts), 2)

    def test_default_correction_allowance_is_zero_and_cannot_change_on_resume(self):
        simulator = ProviderSimulator('none', at=1)
        driver = self.driver(simulator, corrections=0); doc = self.create()
        self.assertEqual(self.engine(doc, driver).run()['error'], 'model_response_rejected')
        driver.max_response_corrections = 1
        with self.assertRaises(Conflict): self.engine(doc, driver).run()
        self.assertEqual(len(simulator.contexts), 1)

    def test_unknown_dispatch_never_uses_correction_allowance(self):
        simulator = ProviderSimulator('timeout')
        driver = self.driver(simulator); doc = self.create()
        result = self.engine(doc, driver).run()
        self.assertEqual(result['error'], 'model_dispatch_unknown')
        self.assertEqual(result['progress']['response_corrections'], 0)
        self.assertEqual(result['progress']['leaf_reads'], 1)
        self.engine(doc, driver).run()
        self.assertEqual(len(simulator.contexts), 2)

    def test_correction_remains_inside_model_budget(self):
        simulator = ProviderSimulator('none', at=1)
        driver = self.driver(simulator); doc = self.create()
        limits = Limits(); limits.model_calls = 1
        result = self.engine(doc, driver, limits=limits).run()
        self.assertEqual(result['error'], 'model_response_rejected')
        self.assertEqual(result['progress']['response_corrections'], 0)
        self.assertEqual(len(simulator.contexts), 1)

    def test_checkpoint_after_rejection_resumes_without_replaying_response(self):
        simulator = ProviderSimulator('multiple')
        driver = self.driver(simulator); doc = self.create()
        def crash(progress):
            if progress['response_corrections']:
                raise RuntimeError('injected process termination')
        with self.assertRaises(RuntimeError): self.engine(doc, driver, on_state=crash).run()
        self.assertEqual(len(simulator.contexts), 2)
        result = self.engine(doc, driver).run()
        self.assertEqual(result['status'], 'complete', result)
        self.assertEqual(len(simulator.contexts), 7)
        self.assertEqual(simulator.contexts[2]['observation']['tool'], 'model_response')


if __name__ == '__main__': unittest.main()
