"""Real chat/API integration with synthetic pharmacy and ETCM source catalogs."""
import copy
import os
import unittest
from unittest.mock import patch
from tests.research_report_fixture import ReportFixture,QUESTION
from tests import test_research_plan_chat as base


class ReportChatTests(unittest.TestCase):
    def setUp(self):
        self.helper=base.ChatTests()
        with patch.object(base,'BusinessFixture',ReportFixture):self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.f=self.helper.f;self.client=self.helper.client

    def turn(self,q):return self.helper.turn(q)

    def test_pair_chat_compiles_runs_and_restores_without_model_calls(self):
        with patch('tcm.intent_planner.plan_question',side_effect=AssertionError('Unexpected model call')):
            data=self.turn(QUESTION).json()['assistant']
        self.assertEqual(data['status'],'ok',data)
        self.assertEqual((data['execution']['tool_calls'],data['execution']['action_calls']),(6,1))
        block=data['blocks'][0];self.assertEqual(block['report_kind'],'pair')
        self.assertEqual(block['shared_total'],23)
        self.assertEqual({r['field'] for r in block['field_rows']},{'功能与主治','用法与用量','药用部位'})
        restored=self.client.get('/conversation').json()['assistant']
        self.assertEqual(restored['run_id'],data['run_id']);self.assertEqual(restored['execution'],data['execution'])
        payload=self.client.get('/api/research/runs/'+data['run_id']+'/evidence/obs-1/download')
        self.assertEqual(payload.status_code,200)
        self.assertEqual(len(payload.json()['data']['report']['targets']['items']),23)

    def test_missing_fields_are_partial_and_disable_does_not_start_execution(self):
        data=self.turn(QUESTION.replace('功效和用量','功效和用量和贮藏')).json()['assistant']
        self.assertEqual(data['status'],'partial',data)
        self.assertEqual(data['blocks'][0]['missing'],['Alpha：贮藏','Beta：贮藏'])
        with patch.dict(os.environ,{'ENABLE_PHARMACOPOEIA_RESEARCH':'0'}):
            with patch('tcm.research_plan_run.run_local_actions',side_effect=AssertionError('Disabled tool ran')):
                disabled=self.turn(QUESTION).json()['assistant']
            self.assertEqual(disabled['status'],'unavailable')
            self.assertNotIn('run_id',disabled)
            response=self.client.post('/api/research/runs/actions',json={'scope':self.f.report_scope().model_dump()})
            self.assertEqual(response.status_code,404)

    def test_two_source_choices_keep_separate_slots_and_the_whole_question(self):
        from tcm.chat import execute_turn
        def ambiguous(request,focus):
            value,new_focus=execute_turn(request,focus)
            if request.get('name') in ('Alpha','Beta'):
                other=copy.deepcopy(value['records'][0]);other['source_record_id']='etcm2-local:'+'0'*64
                value.update(status='ambiguous_entity',records=value['records']+[other])
            return value,new_focus
        with patch('tcm.chat.execute_turn',side_effect=ambiguous):
            first=self.turn(QUESTION).json()['assistant'];self.assertEqual(first['status'],'clarification_needed')
            second=self.turn(first['options'][0]['query']).json()['assistant'];self.assertEqual(second['status'],'clarification_needed')
            result=self.turn(second['options'][0]['query']).json()['assistant']
        self.assertEqual(result['status'],'ok',result)
        self.assertEqual(result['blocks'][0]['herbs'],['Alpha','Beta'])
        saved=self.client.get('/api/research/runs/'+result['run_id']).json()
        self.assertEqual(saved['question'],QUESTION)
        self.assertEqual(saved['result']['run']['tool_calls'],6)

    def test_multiple_leaf_tools_and_source_change_invalidate_restore(self):
        data=self.turn('查询药典Alpha的功效，查询Beta的ETCM药用部位').json()['assistant']
        self.assertEqual(data['status'],'ok',data)
        self.assertEqual(data['execution']['tool_calls'],2)
        self.assertEqual([b['tool'] for b in data['blocks']],['pharmacopoeia_lookup','herb_attributes'])
        path=self.f.pharma/'manifest.json';path.write_text(path.read_text()+' ')
        self.assertEqual(self.client.get('/conversation').json()['assistant']['status'],'unavailable')

    def test_extra_requirement_cannot_silently_downgrade_to_common_targets(self):
        data=self.turn(QUESTION+'，再查采购价格').json()['assistant']
        self.assertNotEqual(data['status'],'ok');self.assertNotIn('run_id',data)
