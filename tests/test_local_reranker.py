import copy
import json
from pathlib import Path
import tempfile
import unittest
from tcm.local_reranker import ranked_hits,maybe_rerank,verify_assets,REPO,REVISION


def hits():
    return [{'chunk_id':key,'text':'original '+key,'name':'示例叶','label':'功能','evidence':[{'page':i}]} for i,key in enumerate(['a','b','c'])]


class RerankerTests(unittest.TestCase):
    def test_scores_reorder_without_changing_evidence(self):
        original=hits();before=copy.deepcopy(original)
        ranked=ranked_hits(original,[-2,3,3],[False,True,False],2)
        self.assertEqual([x['chunk_id'] for x in ranked],['b','c'])
        self.assertEqual(ranked[0]['evidence'],original[1]['evidence'])
        self.assertTrue(ranked[0]['rerank_truncated']);self.assertEqual(original,before)

    def test_invalid_scores_never_return_partial_ranking(self):
        for scores in [[0], [0,float('nan'),1],[0,float('inf'),1]]:
            with self.assertRaises(ValueError):ranked_hits(hits(),scores,[False]*3,2)
        with self.assertRaises(ValueError):ranked_hits([hits()[0]]*2,[0,1],[False]*2,2)

    def test_failure_retains_rrf_and_single_candidate_does_not_load_model(self):
        def unavailable(path):raise TimeoutError('private source path')
        result,meta=maybe_rerank('query',hits(),'configured',factory=unavailable)
        self.assertEqual(result,hits());self.assertEqual(meta['fallback'],'original_rrf')
        self.assertNotIn('private',str(meta))
        def forbidden(path):self.fail('singleton must not initialize model')
        self.assertEqual(maybe_rerank('query',hits()[:1],'configured',factory=forbidden)[1]['status'],'not_needed')
        self.assertEqual(maybe_rerank('query',hits(),None,factory=forbidden)[1]['status'],'not_configured')

    def test_success_is_explicit_and_candidates_bounded(self):
        class Fake:
            def __init__(self,path):pass
            def rerank(self,query,items):
                if len(items)!=20:raise AssertionError('candidate budget')
                return items[-6:],{'status':'used'}
        items=[{**hits()[0],'chunk_id':str(i)} for i in range(30)]
        result,meta=maybe_rerank('query',items,'configured',factory=Fake)
        self.assertEqual([x['chunk_id'] for x in result],[str(i) for i in range(14,20)])
        self.assertEqual(meta['status'],'used')

    def test_unpinned_or_incomplete_model_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for receipt in [{'repo':'other','revision':REVISION,'files':[]},
                            {'repo':REPO,'revision':REVISION,'files':[]}]:
                (root/'download.json').write_text(json.dumps(receipt))
                with self.assertRaises(ValueError):verify_assets(root)
