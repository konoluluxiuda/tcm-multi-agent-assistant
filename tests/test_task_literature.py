"""Reference provenance and synthetic metadata contracts; no external retrieval."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
import os
import sqlite3
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from agents.conversation_store import ConversationStore
from tests import test_etcm_enrichment as fixtures
from tcm.research_task_local import LocalRelationTaskEngine,LocalSnapshotChanged,LocalBudget,snapshot
from tcm.research_task_store import ResearchTaskStore,TaskConflict
from tcm.research_task_api import create_router
from tcm.research_task_report import markdown
from tcm.task_literature import assess_candidate


def article(**overrides):
    return {'pmid':'12345','title':'Synthetic single-herb experiment with isolated compound control',
            'abstract':[{'text':'No extract or formula was administered; a synthetic whole herb was examined.'}],
            'publication_types':[],'relations':[],**overrides}


class TaskLiteratureTests(unittest.TestCase):
    def setUp(self):
        with redirect_stdout(io.StringIO()):fixtures.EnrichmentTests.setUp(self)
        env=patch.dict(os.environ,{'TCM_DB':str(self.base),'TCM_DATASET_VERSION':'v1','ETCM_ENRICHMENT_DB':str(self.path),
                                  'ENABLE_RESEARCH_TASKS':'1','RESEARCH_TASK_DB':str(self.root/'tasks.sqlite3')})
        env.start();self.addCleanup(env.stop)
        self.store=ResearchTaskStore(self.root/'tasks.sqlite3');self.engine=LocalRelationTaskEngine(self.store)

    def finished(self):
        t=self.engine.run(self.engine.create(owner='owner',names=['TestHerb','SecondHerb'])['id'])
        selection={g['name']:g['options'][0]['id'] for g in t['pending']['options']}
        t=self.engine.choose(t['id'],selection,revision=t['revision'])
        return self.engine.choose(t['id'],[t['pending']['options'][0]['id']],revision=t['revision'])

    def leads(self):
        t=self.finished();return self.engine.references(t['id'],revision=t['revision'])

    def assess(self,t,a=None,**kwargs):
        return assess_candidate(a or article(),pmid='12345',source_id=t['sources'][0],
            leads=t['reference_leads'],task_key=t['id'],**kwargs)

    def test_local_references_keep_both_scalar_source_anchors_and_no_study_approval(self):
        before=snapshot();t=self.leads();leads=t['reference_leads']
        self.assertEqual(leads['status'],'local_ids_only');self.assertFalse(leads['metadata_fetched'])
        self.assertEqual(len(leads['groups']),2)
        for group in leads['groups']:
            self.assertEqual(group['total'],1);c=group['candidates'][0];self.assertEqual(c['pmid'],'12345')
            raw=json.loads((self.source/group['source']['relative_file_path']).read_text())
            value=raw
            for part in c['json_pointer'].strip('/').split('/'):
                value=value[int(part)] if isinstance(value,list) else value[part]
            self.assertEqual(value,c['source_value']);self.assertIsInstance(value,str)
            self.assertNotEqual(value,c['normalized_row'])
        self.assertEqual(before,snapshot());self.assertEqual(t['status'],'complete');self.assertEqual(t['report']['study_evidence'],[])
        self.assertIn('12345',markdown(t));self.assertIn('未读取文献',markdown(t));self.assertEqual(t['report']['gaps'],[]);self.assertIn('文献',''.join(t['report']['limitations']))

    def test_reference_read_is_persistent_idempotent_bounded_and_versioned(self):
        t=self.leads();reopened=LocalRelationTaskEngine(ResearchTaskStore(self.store.path))
        self.assertEqual(reopened.references(t['id'],revision=t['revision']),t)
        with self.assertRaises(TaskConflict):reopened.references(t['id'],revision=t['revision']-1)
        t2=self.finished();raw=self.store.get(t2['id']);raw['tool_calls']=12;self.store.save(raw)
        with self.assertRaises(LocalBudget):self.engine.references(raw['id'],revision=raw['revision'])
        t3=self.engine.run(self.engine.create(owner='owner',names=['TestHerb','SecondHerb'])['id'])
        with self.assertRaises(ValueError):self.engine.references(t3['id'],revision=t3['revision'])

    def test_changed_catalog_is_not_silently_read_into_an_existing_report(self):
        t=self.finished();before=deepcopy(t)
        with sqlite3.connect(self.path) as con:con.execute("DELETE FROM edges WHERE kind='reference_pmid'")
        with self.assertRaises(LocalSnapshotChanged):self.engine.references(t['id'],revision=t['revision'])
        self.assertEqual(self.store.get(t['id']),before)

    def test_empty_reference_field_does_not_mean_no_research(self):
        with sqlite3.connect(self.path) as con:con.execute("DELETE FROM edges WHERE kind='reference_pmid'")
        t=self.leads();self.assertEqual([g['total'] for g in t['reference_leads']['groups']],[0,0])
        self.assertFalse(t['reference_leads']['metadata_fetched']);self.assertEqual(t['status'],'complete')

    def test_pmid_and_source_must_match_the_frozen_reference_leads(self):
        t=self.leads()
        with self.assertRaises(ValueError):self.assess(t,article(pmid='999'))
        with self.assertRaises(ValueError):assess_candidate(article(),pmid='12345',source_id='foreign',leads=t['reference_leads'],task_key=t['id'])
        a=self.assess(t);self.assertEqual(a['source_reference_anchors'][0]['pmid'],'12345')
        self.assertFalse(a['object_identity_verified']);self.assertFalse(a['target_relation_verified'])

    def test_raw_herb_ingredient_formula_extract_and_negated_cues_are_not_automatic_identity(self):
        t=self.leads()
        for text in ('Synthetic raw herb study','An isolated compound trial','A formula and extract report',
                     'No extract or formula was administered','No object clues here'):
            result=self.assess(t,article(title=text))
            self.assertEqual(result['status'],'needs_object_review');self.assertFalse(result['direct_attribution_allowed'])
            self.assertFalse(result['object_identity_verified']);self.assertFalse(result['quality_appraised'])

    def test_retraction_and_notice_metadata_excluded_and_no_abstract_remains_unknown(self):
        t=self.leads()
        for publication_types in (['Retracted Publication'],['Retraction of Publication'],['Published Erratum']):
            result=self.assess(t,article(publication_types=publication_types))
            self.assertEqual(result['status'],'excluded_source_notice');self.assertTrue(result['exclusion_flags'])
        result=self.assess(t,article(abstract=[]));self.assertFalse(result['has_abstract']);self.assertFalse(result['direct_attribution_allowed'])
        result=self.assess(t,article(relations=[{'ref_type':'ExpressionOfConcernIn'}]))
        self.assertIn('has_expression_of_concern',result['metadata_flags']);self.assertFalse(result['direct_attribution_allowed'])

    def test_quotes_require_exact_frozen_source_and_user_claim_never_becomes_verification(self):
        t=self.leads();a=article();note={'pmid':'12345','kind':'raw_herb','relation':'same_object_claimed',
            'source_field':'title','abstract_index':None,'quote':'Synthetic single-herb experiment'}
        result=self.assess(t,a,review=note);self.assertFalse(result['direct_attribution_allowed'])
        self.assertFalse(result['user_annotations'][0]['object_identity_verified'])
        with self.assertRaises(ValueError):self.assess(t,a,review={**note,'quote':'Fabricated quotation without source'})
        changed=self.assess(t,article(title=a['title']+' revised'));self.assertNotEqual(result['evidence_id'],changed['evidence_id'])

    def test_deduplication_does_not_shift_original_pmid_positions_and_limit_is_explicit(self):
        from tcm.herbs import import_snapshot
        from scripts.build_etcm_enrichment import build
        for path in self.source.glob('etcm_herbs/*.json'):
            raw=json.loads(path.read_text())
            field=next(f for f in raw['data'][0]['value'] if f['key']=='PubMed')
            field['value']=[' 12345 ','12345','','54321','54322','54323','54324','54325']
            path.write_text(json.dumps(raw))
        import_snapshot(self.source,self.base,'v1')
        fresh=self.root/'rebuilt'
        with redirect_stdout(io.StringIO()):build(self.source,self.base,'v1',fresh,self.entries,self.blocks)
        with patch.dict(os.environ,{'ETCM_ENRICHMENT_DB':str(fresh/'enrichment.sqlite3')}):t=self.leads()
        for group in t['reference_leads']['groups']:
            self.assertEqual(group['total'],6);self.assertEqual(group['returned_count'],5);self.assertTrue(group['truncated'])
            raw=json.loads((self.source/group['source']['relative_file_path']).read_text())
            for candidate in group['candidates']:
                for anchor in candidate['source_anchors']:
                    value=raw
                    for part in anchor['json_pointer'].strip('/').split('/'):
                        value=value[int(part)] if isinstance(value,list) else value[part]
                    self.assertEqual(value,anchor['source_value']);self.assertEqual(value.strip(),candidate['pmid'])
            first=next(c for c in group['candidates'] if c['pmid']=='12345')
            self.assertEqual(len(first['source_anchors']),2)

    def test_api_owner_stale_revision_and_synthetic_task_rejection(self):
        memory=ConversationStore(self.root/'chat.sqlite3');app=FastAPI();app.include_router(create_router(lambda:memory,lambda:'chat'))
        with TestClient(app) as client,TestClient(app) as other:
            t=client.post('/api/research/tasks',json={'mode':'etcm_local','names':['TestHerb','SecondHerb']}).json()
            url='/api/research/tasks/'+t['id'];selection={g['name']:g['options'][0]['id'] for g in t['pending']['options']}
            t=client.post(url+'/choose',json={'revision':t['revision'],'selection':selection}).json()
            t=client.post(url+'/choose',json={'revision':t['revision'],'selection':[t['pending']['options'][0]['id']]}).json()
            self.assertEqual(other.post(url+'/references',json={'revision':t['revision']}).status_code,404)
            r=client.post(url+'/references',json={'revision':t['revision']});self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(r.json()['report']['reference_leads']['status'],'local_ids_only')
            self.assertEqual(client.post(url+'/references',json={'revision':t['revision']}).status_code,409)
            s=client.post('/api/research/tasks',json={}).json()
            self.assertEqual(client.post('/api/research/tasks/'+s['id']+'/references',json={'revision':s['revision']}).status_code,422)


if __name__=='__main__':unittest.main()
