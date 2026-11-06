import unittest
from types import SimpleNamespace
from copy import deepcopy
from tcm.dataops.query_coverage import validate_query_fields
from tests.test_requirements_benchmark import scenario,result
from tcm.evaluation.requirements_benchmark import Expected,score

class SourceChoiceTests(unittest.TestCase):
    def check(self,q,action,fields):
        return validate_query_fields(SimpleNamespace(action=action,fields=fields),q,{'黄芪':['source']})
    def test_shared_unspecified_source_defaults_to_etcm(self):
        q='黄芪的性味和归经可以帮我查一下吗？'
        self.check(q,'herb_field',['properties','flavors','meridians'])
        with self.assertRaises(ValueError):self.check(q,'research',['性味与归经'])
    def test_missing_attribute_still_rejected(self):
        with self.assertRaises(ValueError):self.check('黄芪的性味和归经可以帮我查一下吗？','herb_field',['properties','meridians'])
    def test_explicit_source_preserved(self):
        with self.assertRaises(ValueError):self.check('黄芪药典的性味与归经','herb_field',['properties','flavors','meridians'])
    def test_unrelated_document_requirement_preserved(self):
        with self.assertRaises(ValueError):self.check('黄芪的性味和归经还有注意事项','herb_field',['properties','flavors','meridians'])
    def test_complete_alternative_required_not_mixed_sources(self):
        s=scenario();r=result();alt=s.expected.model_dump()
        for collection in ('requirements','source_assertions'):
            for x in alt[collection]:x['namespace']='pharmacopoeia';x['name']='来源'
        s.alternative_expectations=[Expected.model_validate(alt)]
        for x in r.steps[0].fields+r.steps[0].sources:x['namespace']='pharmacopoeia';x['name']='来源'
        self.assertTrue(score(s,r).completed)
        r.steps[0].sources[0]['value']='fabricated'
        self.assertFalse(score(s,r).completed)
        r=result()
        for coll in (r.steps[0].fields,r.steps[0].sources):coll[0].update(namespace='pharmacopoeia',name='来源')
        self.assertFalse(score(s,r).completed)
