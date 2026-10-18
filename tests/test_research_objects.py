"""Exact frozen-source quotations are traceable, never proof of entity equivalence."""
from copy import deepcopy
import sqlite3
import unittest
from tests import test_evidence_bundle as bundle_tests
from tcm.research_objects import scope_signals, validate_reviews, sha


def article():
    return {'pmid':'123','title':'A single-herb extract combined with a formula',
            'abstract':[{'text':'An isolated compound was used after processing the samples.'}]}


def review(**changes):
    return dict({'pmid':'123','kind':'extract','relation':'scope_differs','source_field':'title',
                 'abstract_index':None,'quote':'single-herb extract'},**changes)


class ObjectScopeTests(unittest.TestCase):
    def test_cues_are_exact_source_positions_and_not_intervention_classification(self):
        a=article();r=scope_signals(a)
        self.assertFalse(r['object_identity_verified']);self.assertFalse(r['direct_attribution_allowed'])
        self.assertIn('extract',{s['kind_hint'] for s in r['signals']})
        self.assertIn('formula',{s['kind_hint'] for s in r['signals']})
        self.assertIn('constituent',{s['kind_hint'] for s in r['signals']})
        for s in r['signals']:
            text=a['title'] if s['source_field']=='title' else a['abstract'][s['abstract_index']]['text']
            self.assertEqual(text[s['start']:s['end']],s['quote']);self.assertEqual(sha(text),s['source_sha256'])

    def test_absent_cues_and_negated_mentions_never_mean_approved_single_herb(self):
        for text in ('No extract or formula was administered.','Unexpected outcomes without an abstract'):
            r=scope_signals({'pmid':'123','title':text,'abstract':[]})
            self.assertEqual(r['status'],'needs_object_review');self.assertFalse(r['object_identity_verified'])
        r=scope_signals({'pmid':'123','title':'Unexpected outcomes','abstract':[]})
        self.assertEqual(r['signals'],[])

    def test_scanning_is_bounded_and_reports_truncation(self):
        r=scope_signals({'pmid':'123','title':'extract '*50,'abstract':[]})
        self.assertEqual(len(r['signals']),12);self.assertTrue(r['truncated'])
        r=scope_signals({'pmid':'123','title':'x'*9000,'abstract':[{'text':'x'}]*25})
        self.assertLessEqual(r['scanned_sources'],21);self.assertTrue(r['truncated'])

    def test_exact_annotation_is_source_hashed_and_keeps_user_claim_unverified(self):
        a=article();raw=deepcopy(a)
        checked=validate_reviews([a],[review(relation='same_object_claimed')],'黄芪的功效','黄芪的功效')[0]
        self.assertEqual(a,raw);self.assertEqual(checked['origin'],'user_annotation')
        self.assertEqual(checked['source_sha256'],sha(a['title']))
        self.assertEqual(a['title'][checked['source_start']:checked['source_end']],checked['quote'])
        self.assertFalse(checked['object_identity_verified']);self.assertFalse(checked['direct_attribution_allowed'])
        self.assertTrue(checked['scope_warning'])

    def test_target_identity_duplicate_and_fabricated_quotations_rejected(self):
        for items,target in [([review()],'甘草的功效'),([review(),review()],'黄芪的功效'),
                             ([review(pmid='456')],'黄芪的功效'),([review(quote='fabricated quotation')],'黄芪的功效'),
                             ([review(abstract_index=0)],'黄芪的功效'),([review(kind='approved')],'黄芪的功效')]:
            with self.assertRaises(ValueError):validate_reviews([article()],items,'黄芪的功效',target)
        self.assertEqual(validate_reviews([article()],[],'黄芪的功效',None),[])

    def test_abstract_section_is_selected_exactly(self):
        a=article();quote=a['abstract'][0]['text'][:30]
        self.assertEqual(validate_reviews([a],[review(source_field='abstract',abstract_index=0,quote=quote)],'q','q')[0]['source_field'],'abstract')
        with self.assertRaises(ValueError):validate_reviews([a],[review(source_field='abstract',abstract_index=1,quote=quote)],'q','q')


class ObjectReviewAPITests(unittest.TestCase):
    def setUp(self):
        bundle_tests.EvidenceBundleTests.setUp(self)
        self.note={'pmid':'123','kind':'extract','relation':'scope_differs','source_field':'title',
                   'abstract_index':None,'quote':'Example herbal study'}
        self.annotated=dict(self.body,object_review_target=self.body['question'],object_reviews=[self.note])

    post=bundle_tests.EvidenceBundleTests.post

    def test_export_contains_source_anchors_and_user_annotation_without_history_or_batch_writes(self):
        history=self.api.get('/conversation').json()
        with sqlite3.connect(self.root/'literature.sqlite3') as db:
            before=db.execute('SELECT payload FROM search_runs').fetchall()
        r=self.post(self.annotated)
        self.assertEqual(r.status_code,200,r.text)
        for text in ('药典锚点','用户对象标注','提取物','对象范围不同','来源文本SHA256','用户主张未验证','对象身份核验：全部未完成'):
            self.assertIn(text,r.text)
        self.assertEqual(self.api.get('/conversation').json(),history)
        with sqlite3.connect(self.root/'literature.sqlite3') as db:self.assertEqual(db.execute('SELECT payload FROM search_runs').fetchall(),before)
        self.assertNotIn('Example herbal study',str(self.memory.load(self.api.cookies[self.app.SESSION_COOKIE_NAME])))

    def test_source_quotation_and_query_must_belong_to_this_export(self):
        for note in (dict(self.note,quote='Not present in this article'),dict(self.note,pmid='456'),dict(self.note,abstract_index=0)):
            self.assertEqual(self.post(dict(self.annotated,object_reviews=[note])).status_code,400)
        self.assertEqual(self.post(dict(self.annotated,object_review_target='其他药材的功效')).status_code,400)
        self.assertEqual(self.post(dict(self.annotated,question='示例叶的功效')).status_code,400)

    def test_schema_does_not_accept_approval_flags_or_arbitrary_source_locations(self):
        for note in (dict(self.note,approved=True),dict(self.note,kind='verified'),dict(self.note,source_field='fulltext'),dict(self.note,abstract_index=True)):
            self.assertEqual(self.post(dict(self.annotated,object_reviews=[note])).status_code,422)
        self.assertEqual(self.post(dict(self.annotated,object_reviews=[self.note]*6)).status_code,422)
        self.assertEqual(self.post(dict(self.annotated,object_reviews=[self.note]*2)).status_code,400)

    def test_current_batch_session_binding_still_applies_to_annotations(self):
        from fastapi.testclient import TestClient
        with TestClient(self.app.app) as other:
            self.assertEqual(other.post('/api/evidence/report',json=self.annotated).status_code,401)
            other.get('/conversation')
            self.assertEqual(other.post('/api/evidence/report',json=self.annotated).status_code,409)
        self.api.post('/chat',json={'query':'/pubmed-clear'})
        self.assertEqual(self.post(self.annotated).status_code,409)

    def test_claimed_same_object_never_grants_identity_or_direct_attribution(self):
        r=self.post(dict(self.annotated,object_reviews=[dict(self.note,relation='same_object_claimed')]))
        self.assertEqual(r.status_code,200,r.text)
        self.assertIn('系统未验证',r.text);self.assertIn('不允许直接疗效归因',r.text)
        self.assertIn('不能直接视作所选原药材等价对象',r.text)
