"""Image upload response ownership; CV/model providers replaced with local fakes."""
import importlib.util
import os
import time
from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from features import FeatureFlags

PNG = b'\x89PNG\r\n\x1a\n'


@unittest.skipUnless(importlib.util.find_spec('langgraph'), 'requires medical container dependencies')
class UploadPrivacyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.tmp.cleanup)
        old=Path.cwd();os.chdir(cls.tmp.name);cls.addClassCleanup(lambda:os.chdir(old))
        env=patch.dict(os.environ,{'ENABLE_IMAGE_ANALYSIS':'0','ENABLE_SPEECH':'0','ENABLE_WEB_SEARCH':'0'})
        env.start();cls.addClassCleanup(env.stop)
        Path('data').mkdir()
        import app
        cls.medical=app

    def setUp(self):
        from fastapi.testclient import TestClient
        from langchain_core.messages import AIMessage
        self.client_type=TestClient
        cfg=SimpleNamespace(features=FeatureFlags(image_analysis=True),api=SimpleNamespace(max_image_upload_size=5))
        item=patch.object(self.medical,'config',cfg);item.start();self.addCleanup(item.stop)
        def query(value, **kwargs):
            if isinstance(value,dict):
                content=Path(value['image']).read_bytes()
                Path(self.medical.SKIN_LESION_OUTPUT,'segmentation_plot.png').write_bytes(content)
            return {'messages':[AIMessage(content='mock analysis')],'agent_name':'SKIN_LESION_AGENT, HUMAN_VALIDATION'}
        item=patch.object(self.medical,'process_query',query);item.start();self.addCleanup(item.stop)

    def test_two_sessions_get_immutable_private_snapshots(self):
        with self.client_type(self.medical.app) as a, self.client_type(self.medical.app) as b, self.client_type(self.medical.app) as anonymous:
            first=a.post('/upload',files={'image':('sample.png',PNG+b'A','image/png')})
            second=b.post('/upload',files={'image':('sample.png',PNG+b'B','image/png')})
            self.assertEqual(first.status_code,200)
            self.assertEqual(second.status_code,200)
            url_a=first.json()['result_image'];url_b=second.json()['result_image']
            self.assertTrue(url_a.startswith('/artifacts/'))
            self.assertNotEqual(url_a,url_b)
            self.assertEqual(a.get(url_a).content,PNG+b'A')
            self.assertEqual(b.get(url_b).content,PNG+b'B')
            self.assertEqual(b.get(url_a).status_code,404)
            self.assertEqual(a.get(url_b).status_code,404)
            self.assertEqual(anonymous.get(url_a).status_code,404)
            self.assertEqual(a.get('/uploads/skin_lesion_output/segmentation_plot.png').status_code,404)
            self.assertEqual(list(Path(self.medical.UPLOAD_FOLDER).iterdir()),[])

    def test_concurrent_generation_keeps_owner_snapshot(self):
        from langchain_core.messages import AIMessage
        def slow_query(value, **kwargs):
            content=Path(value['image']).read_bytes()
            Path(self.medical.SKIN_LESION_OUTPUT,'segmentation_plot.png').write_bytes(content)
            time.sleep(.03)  # Allow another request to overwrite a shared file without the lock.
            return {'messages':[AIMessage(content='mock analysis')],'agent_name':'SKIN_LESION_AGENT, HUMAN_VALIDATION'}
        with patch.object(self.medical,'process_query',slow_query), self.client_type(self.medical.app) as a, self.client_type(self.medical.app) as b:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first=pool.submit(a.post,'/upload',files={'image':('sample.png',PNG+b'A','image/png')})
                second=pool.submit(b.post,'/upload',files={'image':('sample.png',PNG+b'B','image/png')})
                one,two=first.result(),second.result()
            self.assertEqual(one.status_code,200)
            self.assertEqual(two.status_code,200)
            self.assertEqual(a.get(one.json()['result_image']).content,PNG+b'A')
            self.assertEqual(b.get(two.json()['result_image']).content,PNG+b'B')

    def test_chat_cannot_publish_shared_stale_image(self):
        Path(self.medical.SKIN_LESION_OUTPUT,'segmentation_plot.png').write_bytes(PNG+b'other-session')
        with self.client_type(self.medical.app) as client:
            response=client.post('/chat',json={'query':'hello'})
            self.assertEqual(response.status_code,200)
            self.assertNotIn('result_image',response.json())

    def test_failed_analysis_cleans_input_and_publishes_nothing(self):
        before=len(self.medical.private_artifacts.entries)
        with patch.object(self.medical,'process_query',side_effect=RuntimeError('simulated failure')), self.client_type(self.medical.app) as client:
            response=client.post('/upload',files={'image':('sample.png',PNG+b'A','image/png')})
            self.assertEqual(response.status_code,500)
        self.assertEqual(list(Path(self.medical.UPLOAD_FOLDER).iterdir()),[])
        self.assertEqual(len(self.medical.private_artifacts.entries),before)


if __name__ == '__main__':
    unittest.main()
