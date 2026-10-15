"""Feature configuration and real graph/API tests; no paid calls or CV weights."""
import contextlib
import importlib.abc
import importlib.util
import io
import os
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from features import FeatureFlags, env_bool


class FeatureFlagTests(unittest.TestCase):
    def test_defaults_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(all(not s['available'] for s in FeatureFlags.from_env().as_dict().values()))

    def test_boolean_forms_and_invalid_values(self):
        for value in ('1', ' true ', 'YES', 'on'):
            with patch.dict(os.environ, {'CHECK_FLAG': value}): self.assertTrue(env_bool('CHECK_FLAG'))
        for value in ('0', 'false', 'NO', 'off'):
            with patch.dict(os.environ, {'CHECK_FLAG': value}): self.assertFalse(env_bool('CHECK_FLAG'))
        with patch.dict(os.environ, {'CHECK_FLAG': 'maybe'}), self.assertRaises(ValueError): env_bool('CHECK_FLAG')

    def test_credentials_never_enable_features_implicitly(self):
        with patch.dict(os.environ, {'TAVILY_API_KEY':'test-only', 'ELEVEN_LABS_API_KEY':'test-only'}, clear=True):
            self.assertEqual(FeatureFlags.from_env().state('web_search')['reason'], 'disabled')

    def test_enabled_missing_key_is_unavailable(self):
        with patch.dict(os.environ, {'ENABLE_WEB_SEARCH':'1', 'ENABLE_SPEECH':'1'}, clear=True):
            for name in ('web_search', 'speech'):
                self.assertEqual(FeatureFlags.from_env().state(name), {'enabled':True,'available':False,'reason':'missing_credentials'})


@unittest.skipUnless(importlib.util.find_spec('langgraph'), 'requires medical container dependencies')
class OptionalFeatureIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Import blocker proves text startup does not import disabled dependencies.
        cls.forbidden = ('torch', 'torchvision', 'cv2', 'docling', 'elevenlabs', 'pydub',
                         'tavily', 'agents.image_analysis_agent', 'agents.web_search_processor_agent')
        class BlockOptional(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if any(fullname == p or fullname.startswith(p + '.') for p in cls.forbidden):
                    raise AssertionError('Disabled dependency imported: ' + fullname)
        cls.blocker = BlockOptional()
        sys.meta_path.insert(0, cls.blocker)
        cls.addClassCleanup(lambda: sys.meta_path.remove(cls.blocker))
        values = {'ENABLE_IMAGE_ANALYSIS':'0','ENABLE_SPEECH':'0','ENABLE_WEB_SEARCH':'0',
                  'TAVILY_API_KEY':'','ELEVEN_LABS_API_KEY':'',
                  'CHAT_MODEL':'test-only','CHAT_API_KEY':'test-only','CHAT_BASE_URL':'http://127.0.0.1:9/v1',
                  'EMBEDDING_MODEL':'test-only','EMBEDDING_API_KEY':'test-only',
                  'EMBEDDING_BASE_URL':'http://127.0.0.1:9/v1','EMBEDDING_DIM':'1024'}
        cls.env = patch.dict(os.environ, values)
        cls.env.start(); cls.addClassCleanup(cls.env.stop)
        cls.dotenv = patch('dotenv.load_dotenv', lambda *a, **k: None)
        cls.dotenv.start(); cls.addClassCleanup(cls.dotenv.stop)
        from config import Config
        import agents.agent_decision as ad
        cls.ad, cls.Config = ad, Config
        cls.cfg = Config()
        cls.source_root = Path(__file__).resolve().parents[1]

    def setUp(self):
        from langchain_core.messages import AIMessage
        from langchain_core.runnables import RunnableLambda
        from agents.conversation_store import ConversationStore
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.selected = 'CONVERSATION_AGENT'
        self.conversation = RunnableLambda(lambda _: AIMessage(content='offline test reply'))
        decision = RunnableLambda(lambda _: AIMessage(content='{"agent":"'+self.selected+'","confidence":1.0,"reasoning":"test"}'))
        class Guard:
            def __init__(self, *_): pass
            def check_input(self, query): return True, query
            def check_output(self, output, query, chat_history=''): return output
        cfg = self.Config()
        cfg.agent_decision.llm = decision; cfg.conversation.llm = self.conversation
        for item in (patch.object(self.ad,'config',cfg), patch.object(self.ad,'memory',ConversationStore(Path(temp.name) / 'memory.sqlite3')),
                     patch.object(self.ad,'LocalGuardrails',Guard)):
            item.start(); self.addCleanup(item.stop)
        self.cfg = cfg

    def turn(self, query='hello'):
        with contextlib.redirect_stdout(io.StringIO()): return self.ad.process_query(query)

    def test_text_graph_without_optional_imports(self):
        self.assertEqual(self.turn()['messages'][-1].content, 'offline test reply')
        self.assertIsNone(self.cfg.medical_cv)
        self.assertIsNone(self.cfg.speech)
        self.assertIsNone(self.cfg.web_search)
        self.assertIsNone(self.ad.AgentConfig.image_analyzer)
        for prefix in self.forbidden:
            self.assertFalse(any(name == prefix or name.startswith(prefix+'.') for name in sys.modules), prefix)

    def test_disabled_image_request_does_not_call_classifier(self):
        result = self.turn({'text':'', 'image':'/nonexistent/no-weight.jpg'})
        self.assertEqual(result['agent_name'], 'FEATURE_UNAVAILABLE')
        self.assertIn('已关闭', result['messages'][-1].content)

    def test_disabled_web_selection_is_explicit(self):
        self.selected = 'WEB_SEARCH_PROCESSOR_AGENT'
        self.assertEqual(self.turn()['agent_name'], 'FEATURE_UNAVAILABLE')

    def test_rag_low_confidence_does_not_use_disabled_web(self):
        self.selected = 'RAG_AGENT'
        with patch.object(self.ad.rag_runtime,'query',return_value={'response':'insufficient information','confidence':0.1,'sources':[]}):
            result = self.turn()
        self.assertEqual(result['agent_name'],'FEATURE_UNAVAILABLE')
        self.assertIn('联网检索已关闭', result['messages'][-1].content)

    def test_enabled_web_without_key_degrades(self):
        with patch.dict(os.environ, {'ENABLE_WEB_SEARCH':'1','TAVILY_API_KEY':''}):
            self.cfg.features = FeatureFlags.from_env()
        self.selected = 'WEB_SEARCH_PROCESSOR_AGENT'
        self.assertIn('未配置密钥', self.turn()['messages'][-1].content)
        self.assertIsNone(self.cfg.web_search)

    def test_enabled_image_is_lazy_and_cached(self):
        self.cfg.features = FeatureFlags(image_analysis=True)
        module = ModuleType('agents.image_analysis_agent')
        factory = Mock(return_value=object())
        module.ImageAnalysisAgent = factory
        with patch.object(self.ad.AgentConfig, 'image_analyzer', None), patch.dict(sys.modules, {module.__name__: module}):
            self.assertIsNone(self.ad.AgentConfig.image_analyzer)
            self.assertIs(self.ad.AgentConfig.get_image_analyzer(), self.ad.AgentConfig.get_image_analyzer())
            factory.assert_called_once_with(config=self.cfg)

    def test_enabled_speech_client_is_lazy_and_cached(self):
        # Import the app in a temp directory, so tests never write runtime folders in the repository.
        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory); Path('data').mkdir(exist_ok=True)
                import app as medical
                root = ModuleType('elevenlabs'); root.__path__ = []
                module = ModuleType('elevenlabs.client')
                factory = Mock(return_value=object()); module.ElevenLabs = factory
                config = SimpleNamespace(features=FeatureFlags(speech=True,speech_credentials=True),
                                         speech=SimpleNamespace(eleven_labs_api_key='test-only'))
                with patch.object(medical, 'config', config), patch.object(medical, 'client', None), \
                     patch.dict(sys.modules, {'elevenlabs':root,'elevenlabs.client':module}):
                    self.assertIsNone(medical.client)
                    self.assertIs(medical.get_speech_client(),medical.get_speech_client())
                    factory.assert_called_once_with(api_key='test-only')
            finally:
                os.chdir(old_cwd)

    def test_enabled_web_uses_injected_tool(self):
        from langchain_core.messages import AIMessage
        self.cfg.features = FeatureFlags(web_search=True, web_credentials=True)
        self.cfg.web_search = SimpleNamespace(context_limit=20)
        self.selected = 'WEB_SEARCH_PROCESSOR_AGENT'
        module = ModuleType('agents.web_search_processor_agent')
        processor = Mock()
        processor.process_web_search_results.return_value = AIMessage(content='mock tool response')
        module.WebSearchProcessorAgent = Mock(return_value=processor)
        with patch.dict(sys.modules, {module.__name__: module}):
            self.assertEqual(self.turn()['messages'][-1].content, 'mock tool response')
            processor.process_web_search_results.assert_called_once()

    def test_disabled_http_routes_and_text_chat(self):
        from fastapi.testclient import TestClient
        from fastapi.templating import Jinja2Templates
        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                Path('data').mkdir()
                import app as medical
                with patch.object(medical,'config',self.cfg), patch.object(medical,'process_query',self.ad.process_query), \
                     patch.object(medical,'templates',Jinja2Templates(directory=str(self.source_root/'templates'))), \
                     TestClient(medical.app) as client:
                    self.assertEqual(client.get('/health').status_code,200)
                    self.assertFalse(client.get('/features').json()['speech']['available'])
                    for path in ('/upload','/transcribe','/generate-speech'):
                        response = client.post(path)
                        self.assertEqual(response.status_code,403)
                        self.assertEqual(response.json()['status'],'feature_disabled')
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(client.post('/chat',json={'query':'hello'}).json()['response'],'offline test reply')
                    self.assertIn('featureFlags', client.get('/').text)
                    self.assertFalse(medical.cleanup_thread.is_alive())
                    self.cfg.features = FeatureFlags(speech=True)
                    missing = client.post('/transcribe')
                    self.assertEqual(missing.status_code,503)
                    self.assertEqual(missing.json()['reason'],'missing_credentials')
            finally:
                os.chdir(old_cwd)


if __name__ == '__main__':
    unittest.main()
