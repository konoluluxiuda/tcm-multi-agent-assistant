"""Scoring must reject missing goals, wrong evidence and false independent claims."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from scripts.eval_business_closure import Judge,assess,summarize,local_url,target_set,ranking,verify,sha,validate_suite


class BusinessClosureTests(unittest.TestCase):
    def replay_fixture(self,folder):
        import scripts.eval_business_closure as module
        root=Path(folder);baseline=root/'baseline';current=root/'current'
        archive=baseline/'source/scripts';archive.mkdir(parents=True)
        script=Path(module.__file__).read_bytes();(archive/'eval_business_closure.py').write_bytes(script)
        (current/'scripts').mkdir(parents=True);(current/'scripts/eval_business_closure.py').write_bytes(script)
        for name in ('app.py','config.py','features.py'):(current/name).write_text('# new runtime\n')
        for name in ('suite.json','references.json'):(baseline/name).write_text('{"original": true}\n')
        record={'suite_sha256':sha(baseline/'suite.json'),'reference_sha256':sha(baseline/'references.json'),
                'source_sha256':{'scripts/eval_business_closure.py':sha(archive/'eval_business_closure.py')},'inputs':{}}
        (baseline/'frozen.json').write_text(json.dumps(record))
        return module,current,baseline

    def test_replay_preserves_question_oracle_bytes_and_does_not_replace_first_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            module,current,baseline=self.replay_fixture(folder)
            before={name:sha(baseline/name) for name in ('suite.json','references.json','frozen.json')}
            out=Path(folder)/'replay'
            with patch.object(module,'ROOT',current):module.refreeze(SimpleNamespace(baseline=baseline,out=out))
            for name in ('suite.json','references.json'):self.assertEqual((out/name).read_bytes(),(baseline/name).read_bytes())
            self.assertEqual(before,{name:sha(baseline/name) for name in before})
            with patch.object(module,'ROOT',current):verify(out)
            with patch.object(module,'ROOT',current),self.assertRaises(FileExistsError):module.refreeze(SimpleNamespace(baseline=baseline,out=out))

    def test_replay_rejects_changed_scoring_even_if_archive_manifest_matches(self):
        with tempfile.TemporaryDirectory() as folder:
            module,current,baseline=self.replay_fixture(folder)
            old=baseline/'source/scripts/eval_business_closure.py'
            old.write_text(old.read_text()+'\ndef assess(*args): return True\n')
            manifest=json.loads((baseline/'frozen.json').read_text());manifest['source_sha256']['scripts/eval_business_closure.py']=sha(old)
            (baseline/'frozen.json').write_text(json.dumps(manifest))
            with patch.object(module,'ROOT',current),self.assertRaisesRegex(ValueError,'Scoring changed'):
                module.refreeze(SimpleNamespace(baseline=baseline,out=Path(folder)/'bad'))

    def test_boundaries_not_credited_as_completed_business_tasks(self):
        report=summarize([{'role':'positive_task','outcome':'complete'},
            {'role':'positive_task','outcome':'clarification'},{'role':'boundary','outcome':'appropriate_boundary'},
            {'role':'boundary','outcome':'tool_unavailable'}])
        self.assertEqual(report['positive']['complete'],1);self.assertEqual(report['positive']['planned'],2)
        self.assertEqual(report['status'],'completed_with_gaps');self.assertFalse(report['independent_acceptance'])
        self.assertIsNone(report['total_system_cost']);self.assertIsNone(report['provider_tokens'])

    def test_unknown_positive_question_stays_failed_without_rewriting(self):
        case={'role':'positive_task','goals':[{'kind':'overview'}]}
        self.assertEqual(assess(case,403,{},[],Judge({}))['outcome'],'unsupported_intent')
        self.assertEqual(assess(case,200,{'assistant':{'status':'clarification_needed'}},[],Judge({}))['outcome'],'clarification')

    def test_boundary_cannot_hide_executed_facts(self):
        case={'role':'boundary','boundary':'no_facts'}
        self.assertEqual(assess(case,403,{},[],Judge({}))['outcome'],'appropriate_boundary')
        self.assertEqual(assess(case,200,{'assistant':{'status':'clarification_needed'},'research':{'tool_result':{'hits':[{'text':'fact'}]}}},[],Judge({}))['outcome'],'boundary_failed')
        self.assertEqual(assess(case,403,{},[{'tool':'query'}],Judge({}))['outcome'],'boundary_failed')

    def test_pharma_needs_all_requested_fields_and_exact_original_anchor(self):
        hit={'chunk_id':'c1','name':'示例草','label':'功能与主治','raw_text':'合成资料','evidence':[{'block_id':'b1','source':'synthetic'}]}
        refs={'chunks':{'c1':{k:v for k,v in hit.items() if k!='chunk_id'}}}
        case={'role':'positive_task','goals':[{'kind':'pharma_fields','name':'示例草','fields':['功能与主治','用法与用量']}]}
        self.assertEqual(assess(case,200,{'research':{'hits':[hit]}},[],Judge(refs))['outcome'],'requirements_not_met')
        case['goals'][0]['fields']=['功能与主治']
        self.assertEqual(assess(case,200,{},[{'data':{'report':{'hits':[hit]}}}],Judge(refs))['outcome'],'complete')
        forged=copy.deepcopy(hit);forged['evidence'][0]['block_id']='other'
        self.assertEqual(assess(case,200,{'hits':[forged]},[],Judge(refs))['outcome'],'requirements_not_met')

    def test_pointer_resolves_raw_json_and_rejects_hash_or_value_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'raw.json';path.write_text(json.dumps({'a/b':[{'~field':'original'}]}))
            judge=Judge({'source_root':folder});source={'relative_file_path':'raw.json','file_sha256':sha(path)}
            judge.pointer(source,'/a~1b/0/~0field','original')
            from scripts.eval_business_closure import GoalNotMet
            with self.assertRaises(GoalNotMet):judge.pointer(source,'/a~1b/0/~0field','invented')
            with self.assertRaises(GoalNotMet):judge.pointer({**source,'file_sha256':'0'*64},'/a~1b/0/~0field','original')

    def test_ranking_and_source_targets_use_explicit_identifiers(self):
        doc={'data':[{'id':'related_table','value':[{'type':'target','value':[
            {'UniProt Accession Number':['P00001']},{'UniProt Accession Number':['P00001']},
            {'UniProt Accession Number':['P00002','P00003']},{'UniProt Accession Number':['/']},{}]}]}]}
        self.assertEqual(target_set(doc),['P00001'])
        self.assertEqual(ranking(['x','y'],{'a':['x'],'b':['x','y'],'c':['q']}),['b','a'])

    def test_only_loopback_origin_and_no_credentials_or_redirect_destinations(self):
        self.assertEqual(local_url('http://127.0.0.1:7905/'),'http://127.0.0.1:7905')
        for url in ['https://127.0.0.1:7905','http://localhost:7905','http://example.com','http://user@127.0.0.1:7905','http://127.0.0.1/a','http://127.0.0.1/?k=secret']:
            with self.assertRaises(ValueError):local_url(url)

    def test_freeze_tampering_and_independence_claim_are_rejected(self):
        suite=json.loads(Path('eval/business_closure_v1.json').read_text());validate_suite(suite)
        suite['independent_acceptance']=True
        with self.assertRaises(ValueError):validate_suite(suite)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'suite.json').write_text('{}');(root/'references.json').write_text('{}')
            frozen={'suite_sha256':sha(root/'suite.json'),'reference_sha256':sha(root/'references.json'),'source_sha256':{},'inputs':{}}
            (root/'frozen.json').write_text(json.dumps(frozen));verify(root)
            (root/'suite.json').write_text('{"modified":true}')
            with self.assertRaises(ValueError):verify(root)
