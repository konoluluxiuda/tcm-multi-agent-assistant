"""The source preview isolates state and refuses a nonempty public source allowlist."""
import json
from pathlib import Path
import tempfile
import unittest

from scripts.preview_evidence import prepare, preview_environment


class PreviewTests(unittest.TestCase):
    def make_herb_database(self,root):
        from tcm.herbs import import_snapshot
        source=root/'source';(source/'etcm_herbs').mkdir(parents=True)
        (source/'etcm_herbs/Example.json').write_text(json.dumps({'code':1,'data':[
            {'id':'base_information','value':[{'key':'Herb Name in Pinyin','value':'ExampleHerb'}]}]}))
        database=root/'herbs.sqlite3'
        import_snapshot(source,database,'preview-test')
        return database

    def test_profile_forces_synthetic_tools_and_uses_its_own_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            env = prepare(directory)
            self.assertEqual(env['EVIDENCE_ASSISTANT_DATA_MODE'], 'synthetic')
            for name in ('ENABLE_PHARMACOPOEIA_RESEARCH', 'ENABLE_PUBMED',
                         'ENABLE_IMAGE_ANALYSIS', 'ENABLE_SPEECH', 'ENABLE_WEB_SEARCH'):
                self.assertEqual(env[name], '0')
            self.assertEqual(env['CHAT_COOKIE_NAME'], 'tcm_synthetic_preview')
            self.assertEqual(env['LANGSMITH_TRACING'], 'false')
            for name in ('CHAT_MEMORY_DB', 'TCM_DB', 'PUBMED_DB', 'SOURCE_MANIFEST_PATH'):
                self.assertTrue(Path(env[name]).is_relative_to(Path(directory)))
            self.assertNotIn('CHAT_API_KEY', env)
            self.assertEqual(env['EMBEDDING_BASE_URL'], 'http://127.0.0.1:9/v1')
            # Legacy Config can initialize with only the three chat settings supplied.
            from unittest.mock import patch
            from config import Config
            with patch.dict('os.environ', {**env, 'CHAT_MODEL': 'preview-test',
                            'CHAT_API_KEY': 'test-only', 'CHAT_BASE_URL': 'http://127.0.0.1:9/v1'}, clear=True):
                configuration = Config()
                self.assertEqual(configuration.rag.embedding_dim, 1024)
            self.assertEqual(prepare(directory), env)  # Restart preserves the same memory path.

    def test_existing_sources_or_structured_data_are_not_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            env = preview_environment(directory)
            manifest = Path(env['SOURCE_MANIFEST_PATH'])
            manifest.write_text(json.dumps({'version': 1, 'sources': {'unexpected': {}}}))
            with self.assertRaises(ValueError):
                prepare(directory)
            manifest.unlink()
            Path(env['TCM_DB']).touch()
            with self.assertRaises(ValueError):
                prepare(directory)

    def test_local_research_does_not_enable_real_sources_for_model(self):
        from unittest.mock import patch
        from tcm.evidence_assistant import configured_assistant
        from tests.test_evidence_assistant import Model, answer
        with tempfile.TemporaryDirectory() as directory:
            env=prepare(directory,local_research=True)
            self.assertEqual(env['ENABLE_PHARMACOPOEIA_RESEARCH'],'1')
            self.assertEqual(env['EVIDENCE_ASSISTANT_DATA_MODE'],'synthetic')
            self.assertEqual(env['ENABLE_PUBMED'],'0')
            with patch.dict('os.environ',env), \
                 patch('tcm.agent_research.runtime.get_service',side_effect=AssertionError('model read real source')):
                flow=configured_assistant()
                flow.model=Model([{'task':'evidence','field_queries':[{'entity':'示例草','field':'功能与主治'}]},
                                  answer(quote='测试属性A')])
                result=flow.run('示例草一般拿来干什么？')
                self.assertEqual(result['status'],'ok')
                self.assertEqual(result['data_mode'],'synthetic_only')
                self.assertEqual(result['evidence'][0]['chunk_id'],'synthetic-local-efficacy')

    def test_local_herbs_validate_version_readonly_and_keep_ai_synthetic(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);database=self.make_herb_database(root);before=database.read_bytes()
            env=prepare(root/'preview',local_herbs=True,herb_database=database,herb_version='preview-test')
            self.assertEqual(env['TCM_DB'],str(database))
            self.assertEqual(env['TCM_DATASET_VERSION'],'preview-test')
            self.assertEqual(database.read_bytes(),before)
            self.assertEqual(env['EVIDENCE_ASSISTANT_DATA_MODE'],'synthetic')
            self.assertNotEqual(env['TCM_DB'],env['CHAT_MEMORY_DB'])
            from unittest.mock import patch
            from tcm.evidence_assistant import configured_assistant
            from tests.test_evidence_assistant import Model, answer
            with patch.dict('os.environ',env),patch('tcm.herbs.lookup',side_effect=AssertionError('ETCM sent to model')):
                flow=configured_assistant()
                flow.model=Model([{'local_queries':['示例草的功效']},answer(quote='测试属性A')])
                self.assertEqual(flow.run('示例草的功效')['status'],'ok')

    def test_unavailable_herb_database_or_version_fails_before_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);database=self.make_herb_database(root)
            for path,version in [(root/'missing.sqlite3','preview-test'),(database,'missing-version'),(database,'')]:
                with self.subTest(path=path,version=version),self.assertRaises(ValueError):
                    prepare(root/'preview',local_herbs=True,herb_database=path,herb_version=version)
            self.assertFalse((root/'missing.sqlite3').exists())


if __name__ == '__main__':
    unittest.main()
