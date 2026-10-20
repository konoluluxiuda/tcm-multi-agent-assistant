"""Local comparisons retain each source's scope and do not infer missing fields."""
import tempfile
from pathlib import Path
import unittest

from scripts.build_mineru_retrieval import build
from tcm.mineru_chat import ResearchChat, ResearchTool, comparison_request
from tcm.mineru_research import SnapshotChanged
from tests.test_mineru_chat import Service
from tests.test_mineru_retrieval import fixture


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        fixture(self.root/'view');build(self.root/'view',self.root/'index')
        self.service=Service(self.root/'index');self.tool=ResearchTool(self.service)

    def test_two_names_and_fields_are_normalized_in_both_question_orders(self):
        for question in ('比较示例叶和示例叶炭的功效与用量','示例叶与示例叶炭的功效与用量有什么区别？'):
            request=comparison_request(question)
            self.assertEqual(request,{'names':['示例叶','示例叶炭'],'fields':['功能与主治','用法与用量']})

    def test_complete_comparison_preserves_entity_and_citations(self):
        result,context=self.tool.execute('比较示例叶和示例叶炭的功效',None)
        self.assertEqual(result['status'],'candidates');self.assertIsNone(context)
        groups=result['tool_result']['comparison']['groups']
        for group in groups:
            hit=group['cells'][0]['result']['hits'][0]
            self.assertEqual(hit['name'],group['name'])
            self.assertEqual(hit['label'],'功能与主治')
            self.assertTrue(hit['evidence'])
        self.assertEqual(result['llm_calls'],0)
        self.assertEqual(len(self.service.queries),3)

    def test_repeated_field_and_missing_field_are_not_full_success(self):
        repeated,_=self.tool.execute('比较示例叶和示例叶炭的功效与用量',None)
        self.assertEqual(repeated['status'],'partial_evidence')
        self.assertEqual(repeated['tool_result']['comparison']['groups'][0]['cells'][1]['status'],'ambiguous_field')
        missing,_=self.tool.execute('比较示例叶和示例叶炭的功效与注意',None)
        self.assertEqual(missing['status'],'partial_evidence')
        for group in missing['tool_result']['comparison']['groups']:
            self.assertEqual(group['cells'][1]['status'],'field_not_observed')
            self.assertEqual(group['cells'][1]['result']['hits'],[])

    def test_unrecognized_entity_or_preparation_is_not_replaced(self):
        for question,status in [('比较示例叶和外星测试草的功效','name_not_resolved'),
                                ('比较酒示例叶和示例叶炭的功效','variant_scope_unresolved')]:
            self.service.queries.clear()
            result,context=self.tool.execute(question,None)
            self.assertEqual(result['status'],status);self.assertIsNone(context)
            self.assertEqual(len(self.service.queries),1)

    def test_unsupported_scope_is_clarified_without_retrieval(self):
        for question in ('比较示例叶和示例叶炭', '比较示例叶和示例叶炭的功效与用量与注意',
                         '比较示例叶和示例叶炭哪个更适合我', '比较示例叶和示例叶炭和第三药的功效'):
            result,_=self.tool.execute(question,None)
            self.assertEqual(result['status'],'clarify_comparison')
        self.assertEqual(self.service.queries,[])

    def test_comparison_clears_single_focus_and_stores_only_summary(self):
        chat=ResearchChat(self.service,self.root/'memory.sqlite3');cookie=chat.session(None)
        chat.turn(cookie,'示例叶炭的功效',0)
        chat.turn(cookie,'比较示例叶和示例叶炭的功效',1)
        snapshot=chat.store.load_serialized(cookie)
        self.assertIsNone(snapshot.tcm_context)
        self.assertNotIn('测试用途',str(snapshot.messages))
        self.assertEqual(chat.turn(cookie,'它的用量',2)['status'],'clarify_entity')

    def test_snapshot_failure_during_comparison_does_not_commit(self):
        chat=ResearchChat(self.service,self.root/'memory.sqlite3');cookie=chat.session(None)
        original=self.service.search
        def change_after_search(*args,**kwargs):
            result=original(*args,**kwargs)
            if len(self.service.queries)==2:self.service.available=False
            return result
        self.service.search=change_after_search
        with self.assertRaises(SnapshotChanged):
            chat.turn(cookie,'比较示例叶和示例叶炭的功效',0)
        self.assertEqual(chat.store.load_serialized(cookie).revision,0)


if __name__=='__main__':
    unittest.main()
