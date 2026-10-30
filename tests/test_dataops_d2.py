from copy import deepcopy
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest

from tests.dataops_fixture import make_sources
from tcm.dataops.baseline import execute
from tcm.dataops.contracts import Goal
from tcm.dataops.data_access import fingerprint
from tcm.dataops.graph import Engine, Limits
from tcm.dataops.model import SyntheticDriver, OpenAIToolDriver
from tcm.dataops.workspace import Workspaces, Conflict


class Driver(SyntheticDriver):
    def __init__(self, actions=None, failure=False):
        self.calls = []; self.actions = actions; self.failure = failure

    def decide(self, context):
        self.calls.append(deepcopy(context))
        if self.failure:
            raise TimeoutError('unknown provider outcome')
        if self.actions:
            return {'tool': self.actions[min(len(self.calls)-1, len(self.actions)-1)],
                    'arguments': {'purpose': '故障注入'}}
        return super().decide(context)


class D2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.records, self.sources = make_sources(self.root)
        self.store = Workspaces(self.root / 'work')

    def create(self, names=('Alpha',), fields=('功能与主治', '注意'), **kw):
        goal = Goal(query={'names': list(names), 'limit': 50}, document_fields=list(fields))
        with self.sources() as src:
            doc, _ = self.store.reserve_agent('owner', kw.pop('key', 'request-0001'), goal.model_dump(),
                                              src.revision, question='核对指定资料并保留来源', **kw)
        return doc

    def engine(self, doc, driver=None, **kw):
        return Engine(self.store, 'owner', doc['id'], self.sources, driver or Driver(), **kw)

    def answer(self, doc, operation='choose'):
        p = doc['pending']
        return dict(question_id=p['question_id'], revision=doc['revision'], operation=operation,
                    candidate_id=p['candidates'][0]['entry_id'] if operation == 'choose' else None)

    def test_unique_observation_completes_without_human(self):
        doc = self.create(); driver = Driver(); result = self.engine(doc, driver).run()
        self.assertEqual(result['status'], 'complete')
        tools = [e['tool'] for e in result['progress']['events']]
        self.assertNotIn('ask_user', tools)
        self.assertEqual(tools[-3:], ['build_artifact', 'verify_delivery', 'finish_task'])
        self.assertEqual(result['result']['model_calls'], len(driver.calls))
        self.assertEqual(result['result']['rows'][0]['cells']['注意']['status'], 'missing')
        self.assertFalse(result['result']['rows'][0]['identity_verified'])
        self.assertTrue((self.store.root / doc['id'] / 'graph.sqlite3').is_file())

    def test_ambiguous_observation_waits_and_survives_new_engine(self):
        doc = self.create(('Gamma',)); driver = Driver()
        waiting = self.engine(doc, driver).run()
        self.assertEqual(waiting['status'], 'waiting_input')
        self.assertEqual(waiting['progress']['model_calls'], 3)
        self.assertIsNone(waiting.get('artifact'))
        self.store = Workspaces(self.root / 'work')
        new_driver = Driver()
        result = self.engine(doc, new_driver).run(self.answer(waiting))
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['result']['decisions'][self.records['Gamma']['source_record_id']], 'entry-2')
        self.assertEqual(result['result']['rows'][0]['cells']['功能与主治']['value'], '测试原文2')
        self.assertFalse(result['result']['rows'][0]['identity_verified'])
        repeated = self.engine(doc, Driver()).run(self.answer(waiting))
        self.assertEqual(repeated['artifact'], result['artifact'])

    def test_stale_revision_candidate_and_owner_rejected_without_poisoning(self):
        doc = self.create(('Gamma',)); waiting = self.engine(doc).run()
        good = self.answer(waiting)
        for field, value in [('question_id', 'f'*32), ('revision', 2), ('candidate_id', 'entry-999')]:
            bad = {**good, field: value}
            with self.assertRaises(Conflict): self.engine(doc).run(bad)
        with self.assertRaises(KeyError):
            Engine(self.store, 'other-owner', doc['id'], self.sources, Driver())
        self.assertEqual(self.engine(doc).run(good)['status'], 'complete')

    def test_changed_execution_version_cannot_resume_old_checkpoint(self):
        doc = self.create(('Gamma',)); waiting = self.engine(doc).run()
        changed = self.engine(doc); changed.implementation_revision = 'another-implementation'
        with self.assertRaises(Conflict): changed.run(self.answer(waiting))
        self.assertEqual(self.engine(doc).run(self.answer(waiting))['status'], 'complete')

    def test_skip_and_cancel_do_not_invent_data(self):
        doc = self.create(('Gamma',)); waiting = self.engine(doc).run()
        result = self.engine(doc).run(self.answer(waiting, 'skip'))
        self.assertEqual(result['result']['rows'][0]['mapping_status'], 'ambiguous')
        self.assertIsNone(result['result']['rows'][0]['cells']['功能与主治']['value'])
        other = self.create(('Gamma',), key='request-0002')
        waiting = self.engine(other).run()
        cancelled = self.engine(other).run(self.answer(waiting, 'cancel'))
        self.assertEqual(cancelled['status'], 'cancelled'); self.assertIsNone(cancelled.get('artifact'))

    def test_unknown_dispatch_is_not_resent_even_after_restart(self):
        doc = self.create(); driver = Driver(failure=True)
        result = self.engine(doc, driver).run()
        self.assertEqual((result['status'], result['error']), ('interrupted', 'model_dispatch_unknown'))
        fresh = Driver(); self.engine(doc, fresh).run()
        self.assertEqual(len(fresh.calls), 0)
        self.assertEqual(result['progress']['model_diagnostics'], {'exception_type': 'TimeoutError'})

    def test_known_invalid_native_response_is_recorded_without_replay(self):
        from langchain_core.messages import AIMessage
        marker = 'DO_NOT_RETAIN_REPLY_ARGUMENTS'
        one = {'id': 'c1', 'name': 'query_records', 'args': {'purpose': marker}}
        responses = [AIMessage(content=marker),
            AIMessage(content=marker, tool_calls=[one, {**one, 'id': 'c2'}]),
            AIMessage(content=marker, tool_calls=[one], invalid_tool_calls=[
                {'id': 'bad', 'name': 'query_records', 'args': marker, 'error': marker}])]
        for number, reply in enumerate(responses):
            with self.subTest(number=number):
                class Chat:
                    max_retries = 0
                    max_tokens = 1000
                    calls = 0
                    def bind_tools(inner, *args, **kwargs): return inner
                    def invoke(inner, messages):
                        inner.calls += 1
                        return reply
                chat = Chat(); adapter = OpenAIToolDriver(chat, authorized=True)
                doc = self.create(key='invalid-response-' + str(number))
                result = self.engine(doc, adapter).run()
                self.assertEqual((result['status'], result['error']), ('partial', 'model_response_rejected'))
                self.assertEqual(result['progress']['leaf_reads'], 0)
                self.assertIsNone(result['artifact'])
                self.assertNotIn(marker, json.dumps(result))
                self.engine(doc, adapter).run()
                self.assertEqual(chat.calls, 1)
                self.assertEqual(result['progress']['model_diagnostics']['valid_tool_calls'], len(reply.tool_calls))

    def test_read_timeout_observation_allows_one_bounded_retry(self):
        doc = self.create(); attempts = []; factory = self.sources
        class OnceTimeout:
            def __enter__(inner):
                inner.src = factory().__enter__(); original = inner.src.query
                def query(spec):
                    attempts.append(1)
                    if len(attempts) == 1: raise TimeoutError('temporary local read')
                    return original(spec)
                inner.src.query = query
                return inner.src
            def __exit__(inner, *args): inner.src.__exit__(*args)
        result = Engine(self.store, 'owner', doc['id'], OnceTimeout, Driver()).run()
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(len(attempts), 2)
        self.assertTrue(any(e['status'] == 'read_timeout' for e in result['progress']['events']))

    def test_response_receipt_replay_after_checkpoint_gap(self):
        doc = self.create(); first = Driver()
        def crash(progress):
            if progress['last_node'] == 'model': raise RuntimeError('process restart')
        with self.assertRaises(RuntimeError): self.engine(doc, first, on_state=crash).run()
        second = Driver(); result = self.engine(doc, second).run()
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['progress']['model_calls'], len(first.calls)+len(second.calls))
        # Also simulate death after a model response but before native checkpoint commit.
        other = self.create(key='request-0002'); e = self.engine(other, Driver(['query_records']))
        response, unknown = e._receipt(1, {'test': 'receipt'})
        self.assertFalse(unknown)
        cached, unknown = self.engine(other, Driver(failure=True))._receipt(1, {'test': 'receipt'})
        self.assertEqual(cached, response); self.assertFalse(unknown)

    def test_budget_no_progress_and_false_finish_are_not_success(self):
        doc = self.create(); early = self.engine(doc, Driver(['finish_task'])).run()
        self.assertEqual((early['status'], early['error']), ('partial', 'no_progress'))
        self.assertIsNone(early.get('artifact'))
        tiny = Limits(); tiny.model_calls = 1
        other = self.create(key='request-0002')
        result = self.engine(other, limits=tiny).run()
        self.assertEqual(result['error'], 'model_budget')
        tiny = Limits(); tiny.leaf_reads = 0
        other = self.create(key='request-0003')
        result = self.engine(other, limits=tiny).run()
        self.assertEqual(result['status'], 'partial')

    def test_strict_completion_and_unknown_names(self):
        doc = self.create(('NoSuchHerb',))
        result = self.engine(doc).run()
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['result']['coverage']['unknown_names'], ['NoSuchHerb'])
        goal = Goal(query={'names': ['Alpha']}, completion_policy='require_all_fields')
        with self.sources() as src:
            strict, _ = self.store.reserve_agent('owner', 'strict-completion', goal.model_dump(), src.revision,
                                                 question='要求所有字段补齐')
        result = self.engine(strict).run()
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(result['artifact'])
        self.assertEqual(result['result']['goal']['completion_policy'], 'require_all_fields')

    def test_incremental_v2_reads_new_fields_only_and_preserves_v1(self):
        goal = Goal(query={'names': ['Alpha', 'Beta'], 'limit': 50})
        with self.sources() as src:
            parent, _ = execute(self.store, 'owner', 'baseline-1', src, goal)
        old_path, _ = self.store.artifact('owner', parent['id']); old_hash = fingerprint(old_path)
        doc = self.create(('Alpha', 'Beta'), fields=('功能与主治', '注意', '贮藏'),
                          parent_id=parent['id'], expected_revision=1)
        calls = []
        factory = self.sources
        class Spy:
            def __enter__(inner):
                inner.src = factory().__enter__()
                original = inner.src.inspect
                def inspected(record, fields, **kw):
                    calls.append((record['source_record_id'], list(fields)))
                    return original(record, fields, **kw)
                inner.src.inspect = inspected
                return inner.src
            def __exit__(inner, *args): inner.src.__exit__(*args)
        e = Engine(self.store, 'owner', doc['id'], Spy, Driver())
        result = e.run()
        self.assertEqual(result['status'], 'complete')
        self.assertEqual([fields for _, fields in calls], [['贮藏'], ['贮藏']])
        self.assertEqual(result['revision'], 2)
        self.assertEqual(result['artifact']['filename'], 'v2.xlsx')
        self.assertEqual(result['artifact']['verification']['sheets'], 5)
        self.assertTrue(all(d['field'] == '贮藏' for d in result['result']['diff']))
        self.assertEqual(fingerprint(old_path), old_hash)
        self.assertEqual(result['progress']['reused_cells'], 8)
        self.assertEqual(result['progress']['leaf_reads'], 3)
        with self.assertRaises(Conflict):
            self.create(('Alpha',), key='concurrent-edit', parent_id=parent['id'], expected_revision=1)

    def test_refresh_invalidates_all_document_fields_for_requested_record(self):
        parent = self.engine(self.create(('Alpha', 'Beta'))).run()
        sid = self.records['Alpha']['source_record_id']
        child = self.create(('Alpha', 'Beta'), parent_id=parent['id'], expected_revision=1,
                            refresh_source_ids=[sid], key='request-refresh')
        result = self.engine(child).run()
        inspections = [e for e in result['progress']['events'] if e['tool'] == 'inspect_sources'][0]['reads']
        self.assertEqual([r['source_id'] for r in inspections], [sid])
        field_reads = [e for e in result['progress']['events'] if e['tool'] == 'lookup_document_fields'][0]['reads']
        self.assertEqual(field_reads, [{'source_id': sid, 'fields': ['功能与主治', '注意']}])

    def test_artifact_reconciles_after_publish_before_checkpoint(self):
        from tcm.dataops.artifacts import publish
        doc = self.create(); result = self.engine(doc).run()
        path = self.store.root / doc['id']
        same = publish(path, result['result'], reconcile=True)
        self.assertEqual(same, result['artifact'])
        damaged = deepcopy(result['result']); damaged['rows'][0]['name'] = 'wrong'
        with self.assertRaises(ValueError): publish(path, damaged, reconcile=True)

    def test_changed_source_rejected_and_originals_remain_read_only(self):
        doc = self.create()
        with sqlite3.connect(self.root / 'enrichment.sqlite3') as db:
            db.execute("UPDATE metadata SET value='false' WHERE key='build_complete'")
        with self.assertRaises(ValueError): self.engine(doc).run()
        self.assertFalse((self.store.root / doc['id'] / 'v1.xlsx').exists())

    def test_native_provider_adapter_requires_authorization_and_safe_config(self):
        with self.assertRaises(PermissionError): OpenAIToolDriver(object())
        with self.assertRaises(ValueError): OpenAIToolDriver(object(), authorized=True)

    def test_native_ai_message_tool_calls_and_usage_are_used(self):
        from langchain_core.messages import AIMessage
        class FakeChat:
            max_retries = 0
            max_tokens = 1000
            def bind_tools(inner, definitions, **kwargs):
                self.assertFalse(kwargs['parallel_tool_calls'])
                self.assertEqual(kwargs['tool_choice'], 'required')
                self.assertIn('lookup_document_fields', [d['function']['name'] for d in definitions])
                return inner
            def invoke(inner, messages):
                self.assertEqual(messages[0]['role'], 'system')
                return AIMessage(content='', tool_calls=[{'id': 'call-1', 'name': 'query_records',
                    'args': {'purpose': '读取目标记录'}}], usage_metadata={'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15})
        adapter = OpenAIToolDriver(FakeChat(), authorized=True)
        result = self.engine(self.create(), adapter).run()
        # This fake keeps choosing the same tool, so host no-progress validation still wins.
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['progress']['reason'], 'no_progress')

    def test_evidence_read_back_is_bounded_and_observation_is_available(self):
        class Reader(Driver):
            def decide(inner, context):
                if context['evidence_refs'] and not any(c.get('evidence_refs') for c in inner.calls):
                    inner.calls.append(deepcopy(context))
                    return {'tool': 'read_evidence', 'arguments': {'purpose': '核对片段',
                            'evidence_ids': context['evidence_refs'][:1]}}
                return super().decide(context)
        driver = Reader(); result = self.engine(self.create(), driver).run()
        self.assertEqual(result['status'], 'complete')
        events = [e for e in result['progress']['events'] if e['tool'] == 'read_evidence']
        self.assertEqual(len(events), 1)
        self.assertLessEqual(len(events[0]['fragments'][0]['text']), 800)
        observed = [c for c in driver.calls if c.get('field_states')]
        self.assertTrue(any(c['field_states'].get('missing') for c in observed))


if __name__ == '__main__': unittest.main()
