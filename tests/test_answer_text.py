"""Text API answers preserve gaps/provenance without filling checkpoint history."""
import unittest

from tcm.answer_text import plain_answer
from tests import test_assistant_router as router_fixture


class TextTests(unittest.TestCase):
    def test_partial_pharmacopoeia_keeps_gap_and_source_page(self):
        hit={'name':'合成草','label':'功能与主治','text':'合成功能原文',
             'evidence':[{'source':{'source_document':'high','source_pdf_page':8}}]}
        r={'response':'原标签','research':{'message':'未检出注意','tool_result':{'hits':[hit]*5}}}
        text=plain_answer(r)
        for expected in ('未检出注意','合成功能原文','第8页','前4条','尚未完成人工审核'):
            self.assertIn(expected,text)
        self.assertLessEqual(len(text),6100)

    def test_evidence_answer_and_assistant_messages_are_preserved(self):
        for result in ({'response':'带引用回答','evidence_answer':{}}, {'response':'助手说明','assistant':{}}):
            self.assertEqual(plain_answer(result),result['response'])


class ApiTextTests(unittest.TestCase):
    setUp=router_fixture.GraphTests.setUp
    turn=router_fixture.GraphTests.turn

    def test_api_field_text_contains_original_but_memory_remains_compact(self):
        response=self.turn('示例叶炭的功效').json()
        hit=response['research']['tool_result']['hits'][0]
        self.assertIn(hit['text'],response['response'])
        snapshot=self.memory.load_serialized(self.client.cookies[self.app.SESSION_COOKIE_NAME])
        self.assertNotIn(hit['text'],str(snapshot.messages))
        self.assertNotIn(hit['text'],str(snapshot.tcm_context))
