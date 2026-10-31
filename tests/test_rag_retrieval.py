from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from tests.rag_release_fixture import fixture, save
from tcm.data_release import build
from tcm.rag_benchmark import read
from tcm.rag_retrieval import ReleaseSearch, interpret, MultiEntryQdrant
from tcm.rag_retrieval_eval import score, anchor_in_hit, summarize


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.config=fixture(self.root/'source');self.release=self.root/'release'
        build(self.config,self.release)
        self.search=ReleaseSearch(self.release);self.addCleanup(self.search.close)

    def test_multi_entry_and_global_search_apply_same_query_constraints(self):
        for method in ('exact','bm25'):
            r=self.search.search('比较甲草与乙草的贮藏',method)
            self.assertEqual(r['status'],'evidence_found')
            self.assertEqual({h['name'] for h in r['hits']},{'甲草','乙草'})
        r=self.search.search('找防潮相关的说明','bm25')
        self.assertEqual(r['status'],'candidates')
        self.assertTrue(r['hits']);self.assertFalse(r['exhaustive'])
        self.assertEqual(self.search.search('找防潮相关的说明','exact')['hits'],[])

    def test_missing_independent_field_never_substitutes_similar_body(self):
        for method in ('exact','bm25','dense','hybrid'):
            r=self.search.search('只查甲草独立的【注意】字段',method)
            self.assertEqual(r['status'],'missing_field');self.assertFalse(r['hits'])
            self.assertEqual(r['encoding_ms'],0)

    def test_uncertain_objects_and_unknown_preparations_never_reach_dense(self):
        for q in ('甲草，也可能是乙草，形式还没确定，查贮藏','查炒甲草的贮藏','查甲草素的贮藏'):
            r=self.search.search(q,'dense')
            self.assertEqual(r['status'],'clarification_required');self.assertFalse(r['hits'])

    def test_reference_only_evidence_and_duplicate_scopes_do_not_become_complete(self):
        original=next(r for r in self.search.rows.values() if r['name']=='甲草' and r['label']=='贮藏')
        changed=dict(original);changed['text']='【贮藏】同乙草。'
        self.search.rows[original['chunk_id']]=changed
        for method in ('exact','bm25','dense','hybrid'):
            self.assertEqual(self.search.search('甲草的贮藏',method)['status'],'reference_unresolved')
        self.search.rows[original['chunk_id']]=original
        self.search.rows['duplicate']={**original,'chunk_id':'duplicate','scope':'prepared_slices'}
        self.assertEqual(self.search.search('甲草的贮藏','dense')['status'],'clarification_required')

    def test_explicit_scope_and_multi_object_missing_field_are_preserved(self):
        result=self.search.search('甲草只查饮片贮藏','dense')
        self.assertEqual(result['status'],'missing_field');self.assertFalse(result['hits'])
        result=self.search.search('甲草与乙草的药典来源段','exact')
        self.assertEqual(result['status'],'partial_evidence')
        self.assertEqual(result['missing_fields'],[{'name':'乙草','label':'origin'}])

    def test_unrequested_reference_in_open_pool_does_not_imply_failed_candidate_search(self):
        original=next(r for r in self.search.rows.values() if r['name']=='甲草' and r['label']=='贮藏')
        self.search.rows[original['chunk_id']]={**original,'text':'【贮藏】同乙草。'}
        r=self.search.search('找贮藏环境要求','bm25')
        self.assertEqual(r['status'],'candidates');self.assertTrue(r['hits'])
        self.assertEqual(r['unresolved_count'],1)

    def test_unregistered_index_wal_cannot_bypass_file_hash(self):
        (self.release/'index/research.sqlite3-wal').write_bytes(b'pending-change')
        with self.assertRaisesRegex(ValueError,'WAL'):ReleaseSearch(self.release)

    def test_label_alternatives_are_or_requirements_but_literal_independent_labels_are_exact(self):
        original=next(r for r in self.search.rows.values() if r['name']=='甲草' and r['label']=='贮藏')
        for label in ('性味','性味与归经'):
            self.search.rows[original['chunk_id']]={**original,'label':label,'text':'【'+label+'】原文甲。'}
            r=self.search.search('甲草的性味与归经','exact')
            self.assertEqual(r['status'],'evidence_found');self.assertEqual(r['missing_fields'],[])
        r=self.search.search('只查甲草独立的【性味】字段','exact')
        self.assertEqual(r['status'],'missing_field')
        self.search.rows['duplicate']={**original,'chunk_id':'duplicate','label':'性味','text':'【性味】另一段。'}
        self.assertEqual(self.search.search('甲草的性味与归经','exact')['status'],'clarification_required')

    def test_query_limits_and_unconfigured_dense_fail_explicitly(self):
        for question,method,k in (('', 'bm25',10),('x'*501,'bm25',10),('甲草','other',10),('甲草','bm25',True)):
            with self.assertRaises(ValueError):self.search.search(question,method,k)
        with self.assertRaisesRegex(ValueError,'backend'):self.search.search('甲草的贮藏','dense')

    def test_benchmark_reference_not_read_by_search(self):
        # The search API does not need or inspect reference answers.
        (self.release/'benchmark_references.json').unlink()
        self.assertEqual(self.search.search('甲草的贮藏')['status'],'evidence_found')
        r=ReleaseSearch(self.release)
        try:self.assertEqual(r.search('甲草的贮藏')['status'],'evidence_found')
        finally:r.close()

    def test_etcm_raw_pointer_and_value_preserved_without_identity_approval(self):
        r=self.search.search('ETCM Alpha药用部位与药典甲草来源段','exact')
        self.assertEqual(r['status'],'evidence_found')
        h=r['structured_hits'][0]
        self.assertEqual(h['raw_value'],'Leaf');self.assertEqual(h['value'],'Leaf')
        self.assertTrue(h['json_pointer'].startswith('/'));self.assertFalse(h['identity_verified'])
        from tcm.rag_benchmark import inside
        path=inside(self.config['source_root'],h['relative_file_path']);path.write_text('{}')
        with self.assertRaisesRegex(ValueError,'changed'):
            self.search.search('ETCM Alpha药用部位与药典甲草来源段','exact')

    def test_quarantine_blocks_automatic_and_explicit_cross_source_mapping(self):
        sid=next(d['source_record_id'] for d in self.search.documents if d['fields']['name_pinyin']['value']=='Alpha')
        self.search.quarantine.add((sid,'e-alpha'))
        for q in ('ETCM Alpha药用部位与药典甲草来源段','ETCM甲草药用部位与药典来源段'):
            r=self.search.search(q,'exact')
            self.assertEqual(r['status'],'clarification_required');self.assertFalse(r['hits'])

    def test_source_and_old_cache_drift_fail_closed(self):
        from types import SimpleNamespace
        with self.assertRaisesRegex(ValueError,'another release'):
            ReleaseSearch(self.release,SimpleNamespace(receipt={'index_sha256':'old-version'}))
        path=self.release/'index/research.sqlite3'
        with path.open('ab') as stream:stream.write(b'drift')
        with self.assertRaisesRegex(ValueError,'artifact'):ReleaseSearch(self.release)

    def test_presentation_instructions_not_new_entity_and_origin_not_provenance(self):
        r=interpret('查甲草的贮藏；保留乙草的限定作为说明',['甲草','乙草'])
        self.assertEqual(r['names'],['甲草'])
        self.assertNotIn('origin',interpret('甲草功效，保留出处和原始记录位置',['甲草'])['fields'])

    def test_raw_anchor_scoring_rejects_wrong_owner_changed_text_and_missing_witness(self):
        case=read(self.release/'benchmark_references.json')['cases'][0]
        blocks={b['id']:b for b in read(Path(self.config['base'])/'blocks.json')}
        result=self.search.search('甲草的贮藏','exact');a=case['relevant_anchors'][0]
        self.assertTrue(anchor_in_hit(a,result['hits'][0],blocks))
        for changes in ({'entry_id':'e-beta'},{'text':'【贮藏】另一个值'},{'evidence':[]}):
            self.assertFalse(anchor_in_hit(a,{**result['hits'][0],**changes},blocks))
        # Renumbered chunks retain raw-source validity.
        result['hits'][0]['chunk_id']='renumbered'
        case['expected_entities']=[{'entry_id':'e-alpha'}]
        metric=score(case,result,blocks)
        self.assertTrue(metric['passed']);self.assertIsNone(summarize([metric])['recall'])
        wrong=deepcopy(result);wrong['hits'][0]['entry_id']='e-beta'
        self.assertFalse(score(case,wrong,blocks)['passed'])

    def test_excluded_tail_cannot_hide_in_relevant_field(self):
        case=read(self.release/'benchmark_references.json')['cases'][0]
        blocks={b['id']:b for b in read(Path(self.config['base'])/'blocks.json')}
        case['excluded_anchors']=[case['title_anchors'][0]]
        result=self.search.search('甲草的贮藏','exact');result['hits'][0]['text']+='甲草'
        self.assertTrue(score(case,result,blocks)['excluded_span_leaks'])


class ReservedProtocolTests(unittest.TestCase):
    def test_freeze_drift_and_repeated_reserved_run_are_rejected(self):
        from scripts.eval_rag_retrieval import claim_reserved
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);release=root/'release';release.mkdir();protocol=root/'protocol.json'
            current={'references_sha256':'same-set','code_sha256':'frozen'}
            save(protocol,{'bindings':current})
            with self.assertRaisesRegex(ValueError,'changed'):
                claim_reserved(release,protocol,{**current,'code_sha256':'changed'},root/'run')
            claim_reserved(release,protocol,current,root/'run')
            other=root/'another-protocol.json';save(other,{'bindings':current})
            with self.assertRaises(FileExistsError):claim_reserved(release,other,current,root/'another-run')


class QdrantMultiEntryTests(unittest.TestCase):
    def test_real_local_sdk_cross_entry_filter_and_numpy_parity(self):
        import numpy as np
        from tcm.local_dense import build_vectors,DenseRetriever
        from tcm.qdrant_dense import build_generation
        from tcm.mineru_retrieval import ResearchRetriever
        class Encoder:
            dimension=2;fingerprint='synthetic';last_truncated=[]
            def encode(self,texts,query=False):
                self.last_truncated=[False]*len(texts)
                return np.asarray([[1,0] for _ in texts],dtype=np.float32)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=fixture(root/'source');r=ResearchRetriever(config['index'])
            try:
                encoder=Encoder();build_vectors(r,encoder,root/'dense')
                dense=DenseRetriever(r,encoder,root/'dense');build_generation(dense,root/'qdrant')
                backend=MultiEntryQdrant(dense,root/'qdrant')
                try:
                    vector=encoder.encode(['合成'])[0]
                    for names in ({'甲草'},{'甲草','乙草'},{'甲草','乙草','丙草'}):
                        allowed=[i for i,x in enumerate(dense.items) if x['name'] in names]
                        self.assertEqual([i for i,_ in backend.rank(vector,10,allowed)],
                                         [i for i,_ in dense.rank(vector,10,allowed)])
                    self.assertEqual(backend.rank(vector,10,[]),[])
                    with self.assertRaises(ValueError):backend.rank(vector,10,None)
                    with self.assertRaises(ValueError):backend.rank(vector,10,[-1])
                finally:backend.close()
            finally:r.close()


if __name__=='__main__':unittest.main()
