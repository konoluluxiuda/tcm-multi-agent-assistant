from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from scripts.check_clean_reproduction import verify_recovery
from scripts.eval_user_chat_replay import relation_bundle
from scripts.run_research_selection_container import start


class CleanReproductionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_recovery_refuses_lost_cookie_history_or_modified_artifact_before_new_work(self):
        data=b'previous verified artifact';sha=hashlib.sha256(data).hexdigest()
        before={'runtime':{'source_revision':'same','started_at':'old'},
                'conversation':{'messages':[{'role':'user','content':'retained query'}]},
                'artifacts':{'work1':sha}}
        for kind in ('lost_history','corrupt_file','same_process','source_drift'):
            posts=[]
            def transport(request):
                if request.method=='POST':posts.append(request);raise AssertionError('Must fail before new task')
                path=request.url.path
                if path=='/api/runtime':return httpx.Response(200,json={
                    'source_revision':'other' if kind=='source_drift' else 'same',
                    'started_at':'old' if kind=='same_process' else 'new','restart_required':False})
                if path=='/conversation':return httpx.Response(200,json={'messages':[]} if kind=='lost_history' else before['conversation'])
                if path=='/api/dataops/workspaces':return httpx.Response(200,json={'items':[{'id':'work1'}]})
                if path.endswith('/artifact'):return httpx.Response(200,content=b'corrupt' if kind=='corrupt_file' else data)
                raise AssertionError(path)
            with self.subTest(kind=kind),httpx.Client(base_url='http://127.0.0.1:9999',transport=httpx.MockTransport(transport)) as client:
                with self.assertRaises(ValueError):verify_recovery(client,self.root,before)
                self.assertFalse(posts)

    def test_partial_relation_answer_does_not_pass_multi_source_multi_kind_requirement(self):
        requirement=[{'kind':k,'source':s} for k in ('ingredient','target') for s in ('Alpha','Beta')]
        def bundle(kind,source):return {'kind':kind,'data':{'total':1,'items':[{'row':'fixture'}]},
            'groups':[{'options':[{'source':{'relative_file_path':'etcm_herbs/'+source+'.json'}}]}]}
        partial={'assistant':{'relations':bundle('ingredient','Alpha')}}
        with self.assertRaises(ValueError):relation_bundle(partial,requirement)
        full={'results':[bundle(k,s) for k in ('ingredient','target') for s in ('Alpha','Beta')]}
        relation_bundle(full,requirement)
        invalid=deepcopy(full);invalid['results'][-1]['data']['items']=[]
        with self.assertRaises(ValueError):relation_bundle(invalid,requirement)

    def test_container_mounts_registered_target_database_readonly_and_disables_model(self):
        release=self.root/'release';release.mkdir();config={}
        for name in ('herbs','enrichment','targets','source_root','pdf_high','pdf_full'):
            path=self.root/name;path.write_text('fixture');config[name]=str(path)
        (release/'sources.private.json').write_text(json.dumps({'config':config}))
        (release/'release.json').write_text(json.dumps({'content_id':'fixture'}))
        with patch('scripts.run_research_selection_container.subprocess.check_output',return_value='container-id\n') as command:
            start('test-image',release,self.root/'state',19999,'new-container')
        args=command.call_args.args[0]
        self.assertIn('ETCM_TARGET_DB='+config['targets'],args)
        self.assertIn(f"type=bind,src={config['targets']},dst={config['targets']},readonly",args)
        self.assertIn('ENABLE_DATAOPS_RESEARCH_GOAL_MODEL=0',args)
        self.assertIn('--read-only',args)
