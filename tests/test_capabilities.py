"""Help reflects deployed gates without claiming a successful data/model run."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tcm.capabilities import describe, dataops_runtime
from tcm.dataops.service import DataOpsService
from tcm.dataops.workspace import Conflict


class CapabilitiesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        source = self.root / 'source.sqlite3'; source.touch()
        index = self.root / 'index'; index.mkdir()
        (index / 'research.sqlite3').touch(); (index / 'manifest.json').write_text('{}')
        env = patch.dict(os.environ, {'ENABLE_DATAOPS': '1', 'ENABLE_DATAOPS_AGENT': '1',
            'DATAOPS_DRIVER': 'synthetic', 'RESEARCH_JOB_DSN': '',
            'ENABLE_PHARMACOPOEIA_RESEARCH': '1', 'ENABLE_RESEARCH_BOOKMARKS': '1',
            'TCM_DB': str(source), 'ETCM_ENRICHMENT_DB': str(source),
            'ETCM_DISEASE_DB': '', 'PHARMACOPOEIA_INDEX': str(index),
            'PHARMACOPOEIA_PDF_HIGH': str(source), 'PHARMACOPOEIA_PDF_FULL': str(source)}, clear=True)
        env.start(); self.addCleanup(env.stop)

    def test_configured_synthetic_mode_is_visible_but_not_claimed_verified(self):
        result = describe()
        self.assertIn('本地模拟模型', result['message'])
        self.assertNotIn('需启用', result['message'])
        self.assertNotIn('疾病目录', result['message'])
        self.assertNotIn(str(self.root), str(result))
        self.assertTrue(result['dataops']['agent'])
        self.assertFalse(result['readiness_checked'])
        self.assertIn('真实LLM自主整理尚未验收通过', ' '.join(result['notes']))

    def test_missing_sources_and_disabled_features_not_advertised_as_available(self):
        with patch.dict(os.environ, {'TCM_DB': str(self.root/'missing'),
            'ENABLE_PHARMACOPOEIA_RESEARCH': '0', 'ENABLE_RESEARCH_BOOKMARKS': '0',
            'ENABLE_DATAOPS_AGENT': '0'}):
            result = describe()
        self.assertEqual(result['message'], '当前可用入口：\n• 数据概览、数据库介绍')
        self.assertFalse(result['dataops']['agent'])

    def test_one_gate_for_help_service_and_unsupported_driver_queue_modes(self):
        for override in ({'ENABLE_DATAOPS':'0'}, {'ENABLE_DATAOPS_AGENT':'0'},
                         {'DATAOPS_DRIVER':'deepseek'}, {'RESEARCH_JOB_DSN':'postgresql://example'}):
            with self.subTest(override=override), patch.dict(os.environ, override):
                self.assertFalse(dataops_runtime()['agent'])
                self.assertFalse(describe()['dataops']['agent'])
                with self.assertRaises(Conflict): DataOpsService.require_agent()
        DataOpsService.require_agent()

    def test_disease_browsing_does_not_require_herb_enrichment(self):
        with patch.dict(os.environ, {'ETCM_DISEASE_DB': os.environ['TCM_DB'],
                                    'ETCM_ENRICHMENT_DB': ''}):
            entries = {x['id']:x for x in describe()['capabilities']}
        self.assertTrue(entries['diseases']['configured'])
        self.assertFalse(entries['disease_ranking']['configured'])

    def test_empty_index_directory_is_not_advertised_as_configured(self):
        (self.root / 'index' / 'manifest.json').unlink()
        entries = {x['id']:x for x in describe()['capabilities']}
        for name in ('pharmacopoeia', 'workbench', 'dataops_agent'):
            self.assertFalse(entries[name]['configured'])

    def test_workbench_fields_do_not_require_pdf_download_files(self):
        with patch.dict(os.environ, {'PHARMACOPOEIA_PDF_HIGH':str(self.root/'missing.pdf')}):
            entries = {x['id']:x for x in describe()['capabilities']}
        self.assertFalse(entries['pharmacopoeia']['configured'])
        self.assertTrue(entries['workbench']['configured'])

    def test_question_model_does_not_claim_native_execution_is_enabled(self):
        release=self.root/'release';release.mkdir();(release/'release.json').write_text('{}')
        with patch.dict(os.environ,{'ENABLE_DATAOPS_RESEARCH':'1','DATAOPS_RESEARCH_DRIVER':'synthetic',
                'ENABLE_DATAOPS_RESEARCH_GOAL_MODEL':'1','RAG_RELEASE_PATH':str(release),
                'RAG_MODEL':'fixture','RAG_API_KEY':'fixture','RAG_BASE_URL':'https://api.deepseek.com'}):
            result=describe();runtime=result['dataops']['research_selection']
            self.assertTrue(runtime['question_model_enabled']);self.assertFalse(runtime['execution_model_enabled'])
            label=next(x['label'] for x in result['capabilities'] if x['id']=='research_selection')
            self.assertEqual(runtime['question_request_limit'],1)
            self.assertIn('问句理解最多1次请求',label);self.assertNotIn('每任务最多10次',label)
            with patch.dict(os.environ,{'DATAOPS_UNDERSTANDING_POLICY':'unified_v2','SEMANTIC_REGEX_GUARDS':'0'}):
                updated=describe();r=updated['dataops']['research_selection']
                self.assertEqual(r['question_request_limit'],2)
                self.assertFalse(r['semantic_regex_guards']);self.assertFalse(r['execution_model_enabled'])
                self.assertIn('问句理解最多2次请求',next(x['label'] for x in updated['capabilities'] if x['id']=='research_selection'))

    def test_fixed_policy_is_available_without_model_credentials(self):
        with patch.dict(os.environ,{'ENABLE_DATAOPS_RESEARCH':'1','DATAOPS_RESEARCH_DRIVER':'fixed'}):
            runtime=dataops_runtime()['research_selection']
            self.assertTrue(runtime['enabled']);self.assertFalse(runtime['external_model_enabled'])
            label=next(x['label'] for x in describe()['capabilities'] if x['id']=='research_selection')
            self.assertIn('本地确定性执行',label);self.assertNotIn('模拟',label)


if __name__ == '__main__': unittest.main()
