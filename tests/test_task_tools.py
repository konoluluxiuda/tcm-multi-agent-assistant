"""Tool boundary failures must never become an empty successful source query."""
from copy import deepcopy
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from tests import test_relation_queries as fixtures
from tcm.research_task_local import LocalRelationTaskEngine, snapshot
from tcm.research_task_store import ResearchTaskStore
from tcm.task_errors import LocalSnapshotChanged, LocalUnavailable, LocalBudget
from tcm.task_tools import LocalTaskTools, ToolResult, tool_specs


class TaskToolsTests(unittest.TestCase):
    def setUp(self):
        fixtures.RelationQueryTests.setUp(self)
        self.store=ResearchTaskStore(self.root/'tool-tasks.sqlite3')
        self.engine=LocalRelationTaskEngine(self.store)

    def task(self):
        return self.engine.create(owner='owner',names=['测试甲草','测试乙草'])

    def selected(self):
        t=self.engine.run(self.task()['id'])
        selection={g['name']:g['options'][0]['id'] for g in t['pending']['options']}
        return self.engine.choose(t['id'],selection,revision=t['revision'])

    def test_allowlist_and_strict_arguments_reject_before_reading(self):
        specs=tool_specs()
        self.assertEqual({s['name'] for s in specs},{'resolve_sources','common_targets','source_references'})
        self.assertTrue(all(s['parameters']['additionalProperties'] is False for s in specs))
        guard=Mock();tools=LocalTaskTools(self.task(),guard)
        for name,args,code in [('delete_data',{},'unknown_tool'),
                ('common_targets',{'page':True},'invalid_arguments'),
                ('common_targets',{'page':'1'},'invalid_arguments'),
                ('common_targets',{'page':1,'source_ids':['foreign']},'invalid_arguments'),
                ('source_references',{'url':'https://example.org'},'invalid_arguments')]:
            result=tools.invoke(name,args)
            self.assertEqual(result.status,'error');self.assertEqual(result.error.code,code)
            self.assertIsNone(result.data)
        guard.assert_not_called()

    def test_readers_cannot_mutate_controller_state_and_events_preserve_payload(self):
        t=self.task();before=deepcopy(t)
        tools=LocalTaskTools(t,self.engine.ensure)
        result=tools.invoke('resolve_sources',{'names':t['names']})
        self.assertEqual(result.status,'ok');self.assertEqual(t,before)
        result.data.groups[0].options.clear()
        self.assertEqual(t,before)
        finished=self.engine.run(t['id']);event=finished['events'][-1]
        self.assertEqual(event['observation']['groups'][0]['name'],t['names'][0])
        self.assertEqual(event['result']['status'],'ok');self.assertEqual(event['result']['version'],1)
        self.assertNotIn('data',event['result']);self.assertGreaterEqual(event['result']['elapsed_ms'],0)

    def test_unconfirmed_or_foreign_sources_are_not_queryable(self):
        t=self.task()
        with patch('tcm.relation_queries.shared_targets',side_effect=AssertionError('Must not read')):
            result=LocalTaskTools(t,self.engine.ensure).invoke('common_targets',{'page':1})
            self.assertEqual(result.error.code,'invalid_state')
        t=self.selected();t['sources'][0]='foreign'
        with patch('tcm.relation_queries.shared_targets',side_effect=AssertionError('Must not read')):
            result=LocalTaskTools(t,self.engine.ensure).invoke('common_targets',{'page':1})
            self.assertEqual(result.error.code,'invalid_state')

    def test_missing_source_is_empty_but_unavailable_never_is(self):
        t=self.task();t['names']=['未知药材','测试乙草']
        result=LocalTaskTools(t,self.engine.ensure).invoke('resolve_sources',{'names':t['names']})
        self.assertEqual(result.status,'empty');self.assertIsNone(result.error)
        self.assertFalse(result.data.groups[0].options)
        with patch('tcm.chat.execute_request',return_value={'status':'database_unavailable','records':[]}):
            result=LocalTaskTools(t,self.engine.ensure).invoke('resolve_sources',{'names':t['names']})
        self.assertEqual(result.status,'error');self.assertEqual(result.error.code,'unavailable')
        self.assertIsNone(result.data)

    def test_empty_intersection_and_database_failure_are_distinct(self):
        t=self.selected()
        with sqlite3.connect(self.database) as con:con.execute('DELETE FROM edges')
        t['snapshot']=snapshot()
        result=LocalTaskTools(t,self.engine.ensure).invoke('common_targets',{'page':1})
        self.assertEqual(result.status,'empty');self.assertEqual(result.data.total,0)
        with patch('tcm.relation_queries.shared_targets',side_effect=sqlite3.OperationalError('/private/path token=secret')):
            result=LocalTaskTools(t,self.engine.ensure).invoke('common_targets',{'page':1})
        self.assertEqual(result.error.code,'unavailable');self.assertIsNone(result.data)
        self.assertNotIn('/private/path',result.model_dump_json());self.assertNotIn('secret',result.model_dump_json())

    def test_snapshot_changed_during_read_discards_result_without_saving(self):
        t=self.task();before=self.store.get(t['id'])
        guard=Mock(side_effect=[None,LocalSnapshotChanged('private snapshot details')])
        result=LocalTaskTools(t,guard).invoke('resolve_sources',{'names':t['names']})
        self.assertEqual(guard.call_count,2);self.assertEqual(result.error.code,'snapshot_changed')
        self.assertIsNone(result.data);self.assertEqual(self.store.get(t['id']),before)

    def test_invalid_output_is_an_error_and_does_not_overwrite_pending_task(self):
        t=self.selected();before=self.store.get(t['id'])
        with patch('tcm.relation_queries.shared_targets',return_value={'total':True,'items':[],'next_offset':None}):
            result=LocalTaskTools(t,self.engine.ensure).invoke('common_targets',{'page':1})
            self.assertEqual(result.error.code,'invalid_result')
            with self.assertRaises(LocalUnavailable):self.engine.page(t['id'],1,revision=t['revision'])
        self.assertEqual(self.store.get(t['id']),before)

    def test_budget_is_checked_before_reading(self):
        t=self.task();t['tool_calls']=t['max_tool_calls'];self.store.save(t)
        with patch('tcm.chat.execute_request',side_effect=AssertionError('Budget must stop I/O')):
            with self.assertRaises(LocalBudget):self.engine.run(t['id'])
        self.assertEqual(self.store.get(t['id']),t)

    def test_optional_references_require_finished_query_and_remain_local(self):
        t=self.task()
        with patch('tcm.task_literature.reference_leads',side_effect=AssertionError('Must not read yet')):
            result=LocalTaskTools(t,self.engine.ensure).invoke('source_references',{})
        self.assertEqual(result.error.code,'invalid_state')
        t=self.selected();t=self.engine.choose(t['id'],[t['pending']['options'][0]['id']],revision=t['revision'])
        result=LocalTaskTools(t,self.engine.ensure).invoke('source_references',{})
        self.assertIn(result.status,('ok','empty'))
        self.assertFalse(result.data.metadata_fetched);self.assertFalse(result.data.target_relation_verified)
        self.assertEqual(self.store.get(t['id']),t)
        t=self.engine.references(t['id'],revision=t['revision'])
        self.assertEqual(t['events'][-1]['args'],{})
        query=next(e for e in t['events'] if e.get('name')=='common_targets')
        self.assertEqual(query['args'],{'page':1})

    def test_result_contract_refuses_successful_data_with_error_status(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            ToolResult(tool='common_targets',status='ok',elapsed_ms=0.0)
        with self.assertRaises(ValidationError):
            ToolResult(tool='common_targets',status='error',elapsed_ms=0.0)
        self.assertIsInstance(json.dumps(tool_specs()),str)


if __name__=='__main__':unittest.main()
