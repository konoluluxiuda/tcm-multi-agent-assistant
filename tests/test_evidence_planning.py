"""Structured planning contracts over synthetic sources, without provider calls."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.build_mineru_retrieval import build
from tcm.evidence_assistant import EvidenceAssistant, Plan
from tcm.mineru_retrieval import ResearchRetriever
from tests.test_evidence_assistant import Model, answer
from tests.test_mineru_retrieval import fixture


class PlanningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        root=Path(cls.temp.name)
        fixture(root/'source')
        path=root/'source/research_view.json'
        view=json.loads(path.read_text())
        fields=view['entries'][1]['fields']
        for label in ('注意','贮藏','性味与归经'):
            fields.append({**fields[0],'label':label,'text':f'【{label}】来源测试文字。',
                           'raw_text':f'【{label}】来源测试文字。'})
        path.write_text(json.dumps(view,ensure_ascii=False))
        from tcm.mineru_pharmacopoeia import digest
        summary=root/'source/summary.json'
        metadata=json.loads(summary.read_text());metadata['research_view_sha256']=digest(path)
        summary.write_text(json.dumps(metadata))
        build(root/'source',root/'index')
        cls.directory=root/'index'

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_structured_fields_reach_real_retriever_with_paraphrases(self):
        cases=[('使用风险','注意'),('放在哪里不容易坏','贮藏'),
               ('服用份量','用法与用量'),('口味及寒热属性','性味与归经'),
               ('一般拿来干什么','功能与主治')]
        retriever=ResearchRetriever(self.directory);self.addCleanup(retriever.close)
        for wording,field in cases:
            with self.subTest(field=field):
                question='示例叶炭'+wording+'？'
                raw={'task':'evidence','field_queries':[{'entity':'示例叶炭','field':field}]}
                expected=retriever.query('示例叶炭的'+field)['hits'][0]['text']
                model=Model([raw,answer(quote=expected)])
                flow=EvidenceAssistant(model,local_tool=lambda q:retriever.query(q))
                result=flow.run(question)
                self.assertEqual(result['status'],'ok')
                self.assertEqual(result['evidence'][0]['title'],'示例叶炭 · '+field)
                self.assertEqual(result['tool_statuses'][0]['query'],'示例叶炭的'+field)
                self.assertEqual(model.calls,2)
                # Serialized plans remain valid, rather than mixing query formats.
                Plan.model_validate(result['plan'])

    def test_stopped_tasks_never_call_tools_or_generate(self):
        for task,status in [('out_of_scope','out_of_scope'),('overview','overview_required'),
                            ('clarify','clarification_needed')]:
            with self.subTest(task=task):
                raw={'task':task}
                if task=='clarify':raw['clarification']='请明确药材和资料范围。'
                model=Model([raw]);tool=Mock(side_effect=AssertionError('tool reached'))
                result=EvidenceAssistant(model,local_tool=tool,literature_tool=tool).run('示例叶炭今天多少钱？')
                self.assertEqual(result['status'],status)
                self.assertEqual(model.calls,1);tool.assert_not_called()
                self.assertIsNone(result['answer'])

    def test_invalid_field_conflicting_task_or_budget_stop_before_tools(self):
        requests=[{'task':'evidence','field_queries':[{'entity':'示例叶炭','field':'价格'}]},
                  {'task':'out_of_scope','pubmed_query':'shopping'},
                  {'task':'clarify'},
                  {'field_queries':[{'entity':'示例叶炭','field':'注意'}],'local_queries':['other']},
                  {'field_queries':[{'entity':'示例叶炭','field':'注意'}]*3}]
        for raw in requests:
            tool=Mock();model=Model([raw])
            result=EvidenceAssistant(model,local_tool=tool).run('示例叶炭的使用风险？')
            self.assertEqual(result['status'],'planning_failed');tool.assert_not_called()

    def test_invented_entity_and_stripped_preparation_prefix_are_rejected(self):
        for question,entity in [('示例叶炭使用风险？','别的药'),('炙示例叶使用风险？','示例叶')]:
            tool=Mock()
            raw={'task':'evidence','field_queries':[{'entity':entity,'field':'注意'}]}
            result=EvidenceAssistant(Model([raw]),local_tool=tool).run(question)
            self.assertEqual(result['status'],'planning_failed');tool.assert_not_called()

    def test_runtime_keeps_longer_indexed_name_in_original_question(self):
        search=Mock(side_effect=AssertionError('parent lookup reached'))
        service=SimpleNamespace(names=['示例叶','示例叶炭'],search=search)
        raw={'task':'evidence','field_queries':[{'entity':'示例叶','field':'注意'}]}
        with patch('tcm.agent_research.runtime.get_service',return_value=service):
            result=EvidenceAssistant(Model([raw])).run('示例叶炭的使用风险？')
        self.assertEqual(result['status'],'no_evidence');search.assert_not_called()
        self.assertEqual(result['tool_statuses'][0]['status'],'name_not_resolved')

    def test_missing_second_field_remains_partial(self):
        raw={'task':'evidence','field_queries':[{'entity':'示例叶炭','field':'功能与主治'},
                                              {'entity':'示例叶炭','field':'注意'}]}
        def tool(query):
            if query.endswith('注意'):return {'status':'field_not_observed','hits':[]}
            return {'status':'candidates','hits':[{'name':'示例叶炭','label':'功能与主治',
                'text':'来源测试文字。','chunk_id':'fixture'}]}
        result=EvidenceAssistant(Model([raw,answer()]),local_tool=tool).run('示例叶炭用途和使用风险？')
        self.assertEqual(result['status'],'partial')
        self.assertIn('示例叶炭的注意',result['message'])


if __name__=='__main__':
    unittest.main()
