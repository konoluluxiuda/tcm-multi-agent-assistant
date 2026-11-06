"""Source drift and self-contained assets, independent of data/model availability."""
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from tcm.runtime_status import ROOT, RuntimeStatus


class RuntimeTests(unittest.TestCase):
    def test_build_context_contains_nested_workbench_and_assets_but_no_private_data(self):
        from scripts.build_research_image import files
        names=files()
        for name in ('tcm/dataops/api.py','tcm/dataops/workbench.html',
                     'assets/web/favicon.svg','assets/web/vendor/bootstrap/bootstrap.min.css',
                     'assets/web/vendor/fontawesome/webfonts/fa-solid-900.woff2'):
            self.assertIn(name,names)
        self.assertFalse([name for name in names if name.startswith(('data/','var/','.env'))])

    def test_container_without_preview_script_can_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('app.py','config.py','features.py'):
                (root/name).write_text('# container')
            self.assertFalse(RuntimeStatus(root).describe()['restart_required'])

    def test_common_runtime_hash_excludes_entrypoint_but_detects_its_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('app.py','config.py','features.py'):(root/name).write_text('# app')
            container=RuntimeStatus(root)
            (root/'scripts').mkdir();(root/'scripts/preview_local_tasks.py').write_text('# cli')
            host=RuntimeStatus(root)
            self.assertEqual(host.source_revision,container.source_revision)
            self.assertNotEqual(host.entrypoint_revision,container.entrypoint_revision)
            self.assertTrue(container.describe()['restart_required'])
            (root/'scripts/preview_local_tasks.py').write_text('# changed cli')
            result=host.describe();self.assertEqual(result['source_revision'],result['disk_revision'])
            self.assertTrue(result['restart_required'])
            self.assertNotEqual(result['entrypoint_revision'],result['disk_entrypoint_revision'])

    def test_optional_host_modules_are_separate_but_still_trigger_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('app.py','config.py','features.py'):(root/name).write_text('# app')
            container=RuntimeStatus(root)
            module=root/'agents/rag_agent/legacy.py';module.parent.mkdir(parents=True);module.write_text('# optional')
            host=RuntimeStatus(root)
            self.assertEqual(host.source_revision,container.source_revision)
            self.assertNotEqual(host.optional_modules_revision,container.optional_modules_revision)
            module.write_text('# optional changed')
            self.assertTrue(host.describe()['restart_required'])
            self.assertEqual(host.describe()['source_revision'],host.describe()['disk_revision'])
            self.assertNotEqual(host.describe()['optional_modules_revision'],host.describe()['disk_optional_modules_revision'])

    def test_changes_additions_and_deletions_mark_original_process_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('app.py','config.py','features.py','scripts/preview_local_tasks.py'):
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# fixture')
            status=RuntimeStatus(root);original=status.source_revision
            self.assertFalse(status.describe()['restart_required'])
            (root/'app.py').write_text('# changed')
            self.assertTrue(status.describe()['restart_required'])
            self.assertEqual(status.describe()['source_revision'],original)
            (root/'app.py').write_text('# fixture')
            (root/'tcm').mkdir();(root/'tcm'/'new.py').write_text('# new')
            self.assertTrue(status.describe()['restart_required'])
            (root/'tcm'/'new.py').unlink()
            self.assertFalse(status.describe()['restart_required'])
            (root/'app.py').unlink()
            self.assertIsNone(status.describe()['restart_required'])
            self.assertEqual(status.describe()['source_check'],'unavailable')

    def test_runtime_and_homepage_assets_are_local_and_manifest_matches(self):
        import app
        with TestClient(app.app) as client:
            response=client.get('/api/runtime');self.assertEqual(response.status_code,200)
            result=response.json();self.assertIn('source_revision',result)
            self.assertNotIn(str(ROOT),str(result));self.assertEqual(response.headers['cache-control'],'no-store')
            html=client.get('/').text
            paths=re.findall(r'(?:src|href)="([^"]+)"',html)
            self.assertFalse([p for p in paths if p.startswith(('http://','https://','//'))])
            for path in paths:
                if path.startswith('/static/'):
                    self.assertEqual(client.get(path).status_code,200,path)
        manifest=json.loads((ROOT/'assets/web/vendor/manifest.json').read_text())
        for path,metadata in manifest.items():
            content=(ROOT/'assets/web/vendor'/path).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(),metadata['sha256'])
            self.assertEqual(len(content),metadata['bytes'])
        for css in (ROOT/'assets/web').rglob('*.css'):
            for url in re.findall(r'url\(([^)]+)\)',css.read_text()):
                url=url.strip('\"\'')
                if url.startswith('data:'):continue
                self.assertFalse(url.startswith(('https:', 'http:', '//')),url)
                self.assertTrue((css.parent/url).is_file(),(css.name,url))

    def test_disabled_synthetic_panel_does_not_probe_a_disabled_endpoint(self):
        import app
        with TestClient(app.app) as client:
            for value, extensions, visible in (('0','0',False),('1','0',False),
                                                ('0','1',False),('1','1',True)):
                with patch.dict('os.environ',{'ENABLE_SYNTHETIC_LOOP':value,'RESEARCH_TOOLS_ONLY':'1',
                                             'SHOW_RESEARCH_EXTENSIONS':extensions,
                                             'ENABLE_NATURAL_ROUTING':'1','ENABLE_RESEARCH_TASKS':'1'}):
                    html=client.get('/').text
                self.assertEqual('src="/research-loop.js"' in html,visible)
                self.assertEqual('id="synthetic-loop-panel"' in html,visible)
