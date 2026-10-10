import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from compare_rag import load_dataset, run_case, summarize


class EvaluationContracts(unittest.TestCase):
    def setUp(self):
        self.row = {'id': 'zh-1', 'question': '资料的来源是什么？',
                    'reference_answer': '应给出原文出处。', 'source': '来源.pdf'}
        self.trace = {}
        self.counter = SimpleNamespace(started=0, completed=0, usage=[])
        def reset():
            self.counter.started = self.counter.completed = 0
            self.counter.usage = []
        self.counter.reset = reset

    def test_retrieval_survives_later_search_failure(self):
        def run(_):
            self.trace['retrieved'] = [{'source': '来源.pdf', 'content': '原文', 'id': 'chunk-7'}]
            self.trace['rag_result'] = {'response': '证据不足', 'confidence': 0.1}
            raise RuntimeError('Search unavailable')
        rec = run_case(self.row, 'full', run, self.trace, self.counter)
        self.assertEqual(rec['status'], 'error')
        self.assertTrue(rec['retrieval_completed'])
        self.assertTrue(rec['source_file_hit'])
        self.assertEqual(rec['retrieved'][0]['id'], 'chunk-7')
        self.assertIsNotNone(rec['rag_result'])

    def test_cases_do_not_inherit_trace(self):
        self.trace['retrieved'] = [{'source': '来源.pdf'}]
        rec = run_case(self.row, 'full', lambda _: {'answer': '回复', 'agent': 'CHAT'}, self.trace, self.counter)
        self.assertFalse(rec['retrieval_completed'])
        self.assertFalse(rec['source_file_hit'])

    def test_source_match_is_exact_not_substring(self):
        def run(_):
            self.trace['retrieved'] = [{'source': '其他来源.pdf'}]
            return {'answer': '回复', 'agent': 'RAG'}
        rec = run_case(self.row, 'simple', run, self.trace, self.counter)
        self.assertFalse(rec['source_file_hit'])
        self.assertIsNone(rec['review']['answer_supported'])

    def test_swallowed_provider_error_not_success(self):
        rec = run_case(self.row, 'simple', lambda _: {'answer': '错误提示', 'agent': 'RAG', 'error': 'Provider failed'}, self.trace, self.counter)
        self.assertEqual(rec['status'], 'error')
        summary = summarize([rec])
        self.assertEqual(summary['successful'], 0)
        self.assertIsNone(summary['successful_latency_median_s'])
        self.assertIsNone(summary['source_file_hit_rate_retrieved'])

    def test_chinese_dataset_and_duplicate_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'qa.json'
            path.write_text(json.dumps([self.row], ensure_ascii=False))
            rows, digest = load_dataset(path)
            self.assertEqual(rows[0]['question'], self.row['question'])
            self.assertEqual(len(digest), 64)
            path.write_text(json.dumps([self.row, self.row]))
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                load_dataset(path)

    def test_empty_dataset_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'qa.json'
            path.write_text('[]')
            with self.assertRaises(ValueError):
                load_dataset(path)


if __name__ == '__main__':
    unittest.main()
