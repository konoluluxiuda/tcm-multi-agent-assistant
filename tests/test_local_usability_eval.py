"""Usability scoring must not count blocked, partial or malformed answers as completion."""
import json
from pathlib import Path
import unittest

from scripts.eval_local_usability import assess, source_key


class References:
    def hits(self,hits,name,field):
        assert hits and all(h['name']==name and h['label']==field for h in hits)


class UsabilityScoringTests(unittest.TestCase):
    def test_unsupported_and_service_failure_are_not_completion(self):
        case={'kind':'herb_target_paths'}
        self.assertEqual(assess(case,403,{},References())['outcome'],'unsupported_intent')
        self.assertEqual(assess(case,503,{},References())['outcome'],'technical_failure')

    def test_comparison_partial_or_missing_cells_cannot_be_scored_complete(self):
        case={'kind':'comparison','names':['甲草','乙草'],'fields':['功能与主治']}
        comparison={'names':case['names'],'fields':case['fields'],'groups':[
            {'name':name,'cells':[{'field':'功能与主治','status':'candidates',
                'result':{'hits':[{'name':name,'label':'功能与主治'}]}}]}for name in case['names']]}
        body={'research':{'tool_result':{'comparison':comparison}}}
        self.assertEqual(assess(case,200,body,References())['outcome'],'completed_local_scope')
        comparison['groups'][1]['cells'][0]['status']='field_not_observed'
        self.assertEqual(assess(case,200,body,References())['outcome'],'partial')
        comparison['groups']=[]
        self.assertEqual(assess(case,200,body,References())['outcome'],'source_or_contract_failure')

    def test_unknown_entity_needs_actual_empty_candidates_not_missing_contract(self):
        case={'kind':'unknown_herb'}
        body={'assistant':{'status':'clarification_needed','relations':{'groups':[{'options':[]}]}}}
        self.assertEqual(assess(case,200,body,References())['outcome'],'expected_clarification')
        body['assistant']['relations']['groups']=[]
        self.assertEqual(assess(case,200,body,References())['outcome'],'source_or_contract_failure')

    def test_reference_decimal_comparison_is_exact_without_serving_index_keys(self):
        def row(mass):return {'Ingredient Name':['X'],'Molecular Formula':'C2H6','Molecular Weight':mass}
        self.assertEqual(source_key(row('30.070')),source_key(row('30.07')))
        self.assertNotEqual(source_key(row('1.00000000000000000000000000001')),source_key(row('1.00000000000000000000000000002')))
        self.assertIsNone(source_key(row(None)))

    def test_dataset_discloses_post_development_authorship(self):
        suite=json.loads((Path(__file__).resolve().parents[1]/'eval/local_usability_v1.json').read_text())
        self.assertFalse(suite['independent_acceptance'])
        self.assertEqual(len(suite['cases']),12)
        self.assertEqual(sum(c['origin']=='user_reported'for c in suite['cases']),2)
        self.assertEqual(len({c['id']for c in suite['cases']}),12)


if __name__=='__main__':unittest.main()
