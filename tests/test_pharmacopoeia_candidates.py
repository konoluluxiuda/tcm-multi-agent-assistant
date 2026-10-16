"""Field boundary, evidence integrity and non-negative missing semantics."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from tcm.pharmacopoeia import build_candidates, extract_fields, MISSING, query_candidates, selected_lines


def line(text, index=0):
    return {'text':text,'score':.99,'pdf_box':[[1,index+1],[90,index+1],[90,index+2],[1,index+2]],
            'source_key':'test-1-left','line_index':index,'pdf_page':1,'printed_page':3,
            'document':'test','column':'left','source_sha256':'a'*64}


def sample():
    return [line(t,i) for i,t in enumerate(['草药','Caoyao','HERBA TEST','本品为草药的干燥全草。',
        '【性状】不得混入来源字段。','【性味与归经】原始属性。','【功能与主治】第一行',
        '跨页第二行','【用法与用量】识别原值。','【贮藏】原文。'])]


class ExtractionTests(unittest.TestCase):
    def test_boundary_stops_other_sections_and_keeps_cross_page_evidence(self):
        lines=sample();lines[7].update(pdf_page=2,printed_page=4,source_key='test-2-left')
        fields,issues=extract_fields(lines)
        self.assertEqual(fields['medicinal_part']['occurrences'][0]['raw_text'],'干燥全草')
        self.assertNotIn('不得混入',fields['origin']['occurrences'][0]['raw_text'])
        value=fields['efficacy_indications']['occurrences'][0]
        self.assertEqual(value['raw_text'],'第一行\n跨页第二行')
        self.assertEqual([e['pdf_page'] for e in value['evidence']],[1,2])
        self.assertNotIn('识别原值',value['raw_text'])

    def test_missing_cautions_does_not_claim_no_contraindications(self):
        fields,_=extract_fields(sample())
        self.assertEqual(fields['cautions']['state'],MISSING)
        self.assertEqual(fields['cautions']['occurrences'],[])
        self.assertIn('不表示无禁忌',fields['cautions']['message'])

    def test_repeated_headings_remain_separate_candidates(self):
        fields,issues=extract_fields(sample()+[line('【注意】第一条'),line('【检查】别的内容'),line('【注意】第二条')])
        self.assertEqual([o['raw_text'] for o in fields['cautions']['occurrences']],['第一条','第二条'])
        self.assertTrue(any(i['code']=='multiple_field_occurrences' and i['blocking'] for i in issues))

    def test_malformed_heading_and_empty_field_are_unresolved(self):
        fields,issues=extract_fields(sample()+[line('【注意】'),line('【贮藏】存放')])
        self.assertEqual(fields['cautions']['state'],'unresolved_ocr_value')
        self.assertTrue(any(i['code']=='empty_field_block' for i in issues))
        fields,issues=extract_fields(sample()+[line('【注意]错误括号'),line('不可当作上一字段继续。')])
        self.assertTrue(any(i['code']=='malformed_heading' for i in issues))
        self.assertNotIn('错误括号',fields['storage']['occurrences'][0]['raw_text'])

    def test_foreign_entry_is_blocked_before_field_can_bleed(self):
        lines=sample()+[line('另一味药'),line('Lingyiweiyao'),line('OTHER HERBA'),line('本品为其他药。'),line('【注意】不能混入')]
        fields,issues=extract_fields(lines)
        self.assertEqual(fields['cautions']['state'],MISSING)
        self.assertNotIn('另一味药',fields['storage']['occurrences'][0]['raw_text'])
        self.assertTrue(any(i['code']=='possible_multiple_entries' and i['blocking'] for i in issues))

    def test_raw_ocr_is_not_replaced_with_reference_spelling(self):
        lines=sample();lines[3]['text']='本品为Liriope spicala的干燥块根。'
        fields,_=extract_fields(lines)
        self.assertIn('spicala',fields['origin']['occurrences'][0]['raw_text'])
        self.assertNotIn('spicata',fields['origin']['occurrences'][0]['raw_text'])
        lines[3]['text']='本品为草药的非干燥果皮。'
        self.assertEqual(extract_fields(lines)[0]['medicinal_part']['state'],'unresolved_ocr_value')


class CandidateIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)
        self.suite={'documents':{'test':{'path':'local.pdf','sha256':'a'*64,'printed_pages':{'1':3}}},
          'cases':[{'id':'Caoyao','name_zh_candidate':'草药','etcm_source_record_id':'id:1','mapping_status':'pending',
                    'segments':[{'document':'test','pdf_page':1,'column':'left','y_range':[0,1]}],
                    'expected_fragments':{'name':'THIS IS NOT EXTRACTION INPUT'}}]}
        self.raw={'metadata':{'key':'test-1-left','pdf_page':1,'printed_page':3,'column':'left',
                            'source':self.suite['documents']['test']},
                  'lines':[{**l,'center_y_fraction':.5} for l in sample()]}
        self.write()

    def write(self):
        (self.path/'test-1-left.json').write_text(json.dumps(self.raw))

    def test_expected_answers_do_not_generate_fields(self):
        first=build_candidates(self.suite,self.path,[])
        self.suite['cases'][0]['expected_fragments']={'properties_meridians':'invented'}
        self.assertEqual(first,build_candidates(self.suite,self.path,[]))
        self.assertEqual(first['records'][0]['review_status'],'pending_human_review')

    def test_source_mismatch_and_invalid_geometry_fail_closed(self):
        for modify in [lambda r:r['metadata'].update(pdf_page=2),
                       lambda r:r['metadata']['source'].update(sha256='wrong'),
                       lambda r:r['metadata'].update(printed_page=99),
                       lambda r:r['lines'][0].update(pdf_box=[[float('nan'),1]]*4),
                       lambda r:r['lines'][0].update(score=2)]:
            saved=copy.deepcopy(self.raw);saved_suite=copy.deepcopy(self.suite)
            modify(self.raw);self.write()
            with self.assertRaises(ValueError):selected_lines(saved_suite,saved_suite['cases'][0],self.path)
            self.raw=saved;self.suite=saved_suite;self.write()

    def test_flag_preserves_raw_value_and_requires_matching_source_evidence(self):
        note={'case_id':'Caoyao','source_key':'test-1-left','observed_fragment':'原始属性','visual_reference':'不能自动改写'}
        record=build_candidates(self.suite,self.path,[note])['records'][0]
        self.assertEqual(record['review_status'],'quarantined')
        self.assertEqual(record['fields']['properties_meridians']['occurrences'][0]['raw_text'],'原始属性。')
        note['observed_fragment']='not present'
        with self.assertRaises(ValueError):build_candidates(self.suite,self.path,[note])

    def test_query_is_exact_local_scope_and_never_approves_candidates(self):
        data=build_candidates(self.suite,self.path,[])
        result=query_candidates(data,' 草药 ','cautions')
        self.assertEqual(result['status'],'unreviewed_candidate')
        self.assertFalse(result['human_signoff'])
        self.assertEqual(result['result']['state'],MISSING)
        self.assertEqual(query_candidates(data,'other','cautions')['status'],'not_found')
        with self.assertRaises(ValueError):query_candidates(data,'草药','unsupported')
        data['records'].append(copy.deepcopy(data['records'][0]))
        self.assertEqual(query_candidates(data,'草药','cautions')['status'],'ambiguous_entity')


class LostPunctuationTests(unittest.TestCase):
    def test_missing_period_does_not_swallow_harvest_instructions(self):
        lines=sample();lines[3]['text']='本品为植物的干燥果皮冬季至次春采收未成熟的果实'
        fields,_=extract_fields(lines)
        self.assertEqual(fields['medicinal_part']['occurrences'][0]['raw_text'],'干燥果皮')
        self.assertIn('冬季',fields['origin']['occurrences'][0]['raw_text'])
