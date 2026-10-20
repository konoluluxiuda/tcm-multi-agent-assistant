"""Offline dense invariants, without installing the optional ONNX/numpy runtime in CI."""
import json
from pathlib import Path
import tempfile
import unittest

from tcm.local_dense import top_indices,DenseRetriever


class DenseTests(unittest.TestCase):
    def test_filter_precedes_top_k_and_ties_are_deterministic(self):
        scores=[0.99,0.8,0.8];ids=['foreign','z-target','a-target']
        self.assertEqual(top_indices(scores,ids,1),[0])
        self.assertEqual(top_indices(scores,ids,1,allowed=[1,2]),[2])
        self.assertEqual(top_indices(scores,ids,1,allowed=[]),[])

    def test_bad_scores_indices_and_limits_are_rejected(self):
        for scores,ids,k,allowed in [([float('nan')],['a'],1,None),([float('inf')],['a'],1,None),
                                     ([1],[],1,None),([1],['a'],True,None),([1],['a'],21,None),
                                     ([1],['a'],1,[2])]:
            with self.assertRaises(ValueError):top_indices(scores,ids,k,allowed)

    def test_scope_failures_do_not_encode_or_fall_back_to_global_search(self):
        class Encoder:
            def encode(self,*a,**kw):raise AssertionError('Scope failure reached encoder')
        class Lexical:
            def __init__(self,status):self.status=status
            def query(self,*a,**kw):return {'status':self.status,'names':[]}
        for status in ('name_not_resolved','multiple_names','variant_scope_unresolved','ambiguous_field','field_not_observed'):
            dense=DenseRetriever.__new__(DenseRetriever);dense.encoder=Encoder();dense.lexical=Lexical(status)
            result=dense.query('待澄清的查询',method='name_dense')
            self.assertEqual(result['status'],status);self.assertEqual(result['hits'],[])
            self.assertEqual(result['query_encoding_ms'],0)

    def test_model_and_cache_integrity_are_checked_without_model_execution(self):
        from tcm.local_dense import verify_model_files,check_cache_manifest,RECIPE
        from tcm.mineru_pharmacopoeia import digest
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);rows=[]
            for filename in ('config.json','tokenizer.json','tokenizer_config.json','onnx/model.onnx'):
                path=root/filename;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture')
                rows.append({'file':filename,'sha256':digest(path)})
            (root/'download.json').write_text(json.dumps({'repo':'Xenova/bge-small-zh-v1.5','revision':'75c43b069aac4d136ba6bc1122f995fedcfd2781','files':rows}))
            verify_model_files(root)
            (root/'onnx/model.onnx').write_text('changed')
            with self.assertRaises(ValueError):verify_model_files(root)
        receipt={'recipe':RECIPE,'index_sha256':'index','model_fingerprint':'model','vectors_sha256':'vectors','items_sha256':'items'}
        check_cache_manifest(receipt,'index','model','vectors','items')
        for key in ('index_sha256','model_fingerprint','vectors_sha256','items_sha256'):
            with self.assertRaises(ValueError):check_cache_manifest({**receipt,key:'changed'},'index','model','vectors','items')
