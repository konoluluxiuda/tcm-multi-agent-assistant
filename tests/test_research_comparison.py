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
        for question in ('比较示例叶和示例叶炭', '比较示例叶和示例叶炭的功效与用量与注意与归经与贮藏',
                         '比较示例叶和示例叶炭哪个更适合我', '比较示例叶和示例叶炭和第三药和第四药的功效'):
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

    def three_herb_service(self):
        import json
        from copy import deepcopy
        from tcm.mineru_pharmacopoeia import digest
        view = self.root/'view'
        data = json.loads((view/'research_view.json').read_text())
        third = deepcopy(data['entries'][1])
        third.update(id='test-third', name_zh_candidate='第三草', raw_name_zh_candidate='第三草')
        for field in third['fields']:
            field['text'] = field['raw_text'] = '【' + field['label'] + '】第三草专属合成记录。'
            field['evidence'][0]['source']['source_pdf_page'] = 9
        data['entries'].append(third)
        (view/'research_view.json').write_text(json.dumps(data, ensure_ascii=False))
        summary = json.loads((view/'summary.json').read_text())
        summary['research_view_sha256'] = digest(view/'research_view.json')
        (view/'summary.json').write_text(json.dumps(summary))
        build(view, self.root/'three-index')
        return Service(self.root/'three-index')

    def test_real_wording_three_herbs_preserves_combined_source_field(self):
        question = '帮我把黄芪、党参和人参放一起看看吧，性味、归经、功效、用量都想比较一下。'
        expected = {'names': ['黄芪','党参','人参'], 'fields': ['性味与归经','功能与主治','用法与用量']}
        self.assertEqual(comparison_request(question), expected)
        self.assertEqual(comparison_request('比较黄芪、党参和人参的性味与归经、功效与用量'), expected)
        from tcm.assistant_router import resolve
        from unittest.mock import patch
        with patch.dict('os.environ', {'ENABLE_NATURAL_ROUTING': '1'}):
            self.assertEqual(resolve(question)['request']['question'], question)

    def test_maximum_grid_preserves_each_entity_and_missing_cells(self):
        service = self.three_herb_service()
        question = '比较示例叶、示例叶炭和第三草的功效、用量、注意、贮藏'
        result, context = ResearchTool(service).execute(question, None)
        self.assertIsNone(context)
        self.assertEqual(result['status'], 'partial_evidence')
        groups = result['tool_result']['comparison']['groups']
        self.assertEqual(len(service.queries), 13)  # one full guard and twelve cells
        self.assertEqual([g['name'] for g in groups], ['示例叶','示例叶炭','第三草'])
        for group in groups:
            self.assertEqual(len(group['cells']), 4)
            for cell in group['cells']:
                for hit in cell['result']['hits']:
                    self.assertEqual(hit['name'], group['name'])
                    self.assertEqual(hit['label'], cell['field'])
            self.assertEqual(group['cells'][2]['status'], 'field_not_observed')
            self.assertEqual(group['cells'][3]['result']['hits'], [])
        third = groups[2]['cells'][0]['result']['hits'][0]
        self.assertIn('第三草专属', third['text'])
        self.assertEqual(third['evidence'][0]['source']['source_pdf_page'], 9)
        self.assertEqual(result['llm_calls'], 0)

    def test_colloquial_list_passes_full_question_guards_without_losing_scope(self):
        service = self.three_herb_service()
        result, _ = ResearchTool(service).execute('帮我把示例叶、示例叶炭和第三草放一起看看吧，功效、用量都想比较一下。', None)
        self.assertEqual(result['status'], 'partial_evidence')
        self.assertEqual(len(result['tool_result']['comparison']['groups']), 3)
        self.assertEqual(len(service.queries), 7)

    def test_three_herb_export_has_all_cells_and_separate_sources(self):
        from tcm.comparison_report import export_comparison
        service = self.three_herb_service()
        text = export_comparison(service, '比较示例叶、示例叶炭和第三草的功效、用量、注意、贮藏', service.receipt['index_sha256'])
        self.assertEqual(text.count('## 药材：'), 3)
        self.assertEqual(text.count('### 请求字段：'), 12)
        self.assertIn('第三草专属', text)
        self.assertIn('partial', text)
        self.assertIn('最多三味药、四个字段', text)
        self.assertEqual(text.count('本单元没有可用候选字段'), 6)

    def test_new_scope_does_not_drop_unknown_or_prepared_third_herb(self):
        for name in ('外星测试草','酒示例叶'):
            self.service.queries.clear()
            result, _ = self.tool.execute('比较示例叶和示例叶炭和'+name+'的功效、注意', None)
            self.assertIn(result['status'], ('name_not_resolved','variant_scope_unresolved'))
            self.assertNotIn('comparison', result.get('tool_result') or {})
            self.assertEqual(len(self.service.queries), 1)

    def test_lists_do_not_guess_entities_or_discard_extra_instructions(self):
        for question in ('比较黄芪和黃芪的功效', '比较黄芪、党参和人参的功效，帮我开处方',
                         '比较黄芪、党参和人参的功效、', '比较黄芪和党参的功效和价格',
                         '比较黄芪和党参的功效，再比较人参的用量',
                         '那它和刚才那个黄芪有什么区别？先看功效和用量，归经也顺便放进去吧。'):
            self.assertIn('error', comparison_request(question))

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
