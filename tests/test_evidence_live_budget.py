"""The authorized batch cannot expand model inputs or per-question call limits."""
import json
import unittest
from types import SimpleNamespace
from scripts.eval_evidence_live import BoundedModel


class Provider:
    def __init__(self):self.calls=0
    def invoke(self,messages):self.calls+=1;return 'fixture'


def messages(payload):
    return [SimpleNamespace(content='system'),SimpleNamespace(content=json.dumps(payload))]


class BudgetTests(unittest.TestCase):
    def test_two_call_limit_and_question_history_boundaries(self):
        provider=Provider();model=BoundedModel(provider,'approved question')
        plan={'question':'approved question','data_mode':'configured_local_sources','available_tools':{}}
        model.invoke(messages(plan));model.invoke(messages(plan))
        with self.assertRaises(ValueError):model.invoke(messages(plan))
        self.assertEqual(provider.calls,2)
        for payload in ({**plan,'question':'other question'},{**plan,'history':['private']}):
            provider=Provider();model=BoundedModel(provider,'approved question')
            with self.assertRaises(ValueError):model.invoke(messages(payload))
            self.assertEqual(provider.calls,0)

    def test_excerpt_group_count_and_length_checked_before_provider_call(self):
        good={'source_group':'traditional','text':'a'*800}
        for evidence in ([good]*7,[{'source_group':'literature','text':'x'}],
                         [{'source_group':'traditional','text':'a'*801}]):
            provider=Provider();model=BoundedModel(provider,'approved question')
            with self.assertRaises(ValueError):
                model.invoke(messages({'question':'approved question','evidence':evidence,'tool_statuses':[]}))
            self.assertEqual(provider.calls,0)
        provider=Provider();model=BoundedModel(provider,'approved question')
        model.invoke(messages({'question':'approved question','evidence':[good]*6,'tool_statuses':[]}))
        self.assertEqual(model.transfers[0]['excerpt_characters'],[800]*6)
