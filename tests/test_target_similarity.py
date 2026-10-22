import json
import sqlite3
import unittest
from unittest.mock import patch

from tests import test_relation_queries as fixtures
from tcm.target_similarity import rank_sets, query, SnapshotChanged
from tcm.chat import execute_request
from tcm.analysis_tools import invoke, SimilarityData


class SetTests(unittest.TestCase):
    def test_hand_calculated_scores_and_metric_tradeoff(self):
        sets={'q':{'a','b'},'small':{'a'},'big':{'a','b','c','d','e'},'other':{'x'},'missing':set()}
        rows=rank_sets('q',sets)
        self.assertEqual([r['source_record_id'] for r in rows],['small','big'])
        self.assertEqual([r['jaccard'] for r in rows],[0.5,0.4])
        self.assertEqual(rows[0]['query_only_ids'],['b'])
        self.assertEqual(rank_sets('q',sets,metric='common_count')[0]['source_record_id'],'big')
        self.assertEqual(rank_sets('missing',sets),[])

    def test_identical_disjoint_and_stable_ties(self):
        sets={'q':{'a','b'},'z':{'a','b'},'a':{'a','b'},'empty':set(),'different':{'c'}}
        self.assertEqual([r['source_record_id'] for r in rank_sets('q',sets)],['a','z'])
        self.assertEqual(rank_sets('q',sets)[0]['jaccard'],1)
        self.assertEqual(len(rank_sets('q',sets,top_k=1)),1)
        for top in (True,0,21,'2'):
            with self.assertRaises(ValueError):rank_sets('q',sets,top_k=top)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        fixtures.RelationQueryTests.setUp(self)
        result=execute_request({'name':'Alpha'})
        self.record=result['records'][0];self.version=result['dataset_version']

    def run_query(self,**kw):
        return query(self.database,self.base,self.record,self.version,tool='herb_target_similarity',**kw)

    def test_real_catalog_dedup_missing_ids_sources_and_typed_result(self):
        data=self.run_query();SimilarityData.model_validate(data)
        profile=data['profile'];self.assertEqual((profile['target_count'],profile['source_row_count'],profile['unidentified_rows'],profile['duplicate_identifier_rows']),(24,26,1,1))
        row=data['items'][0];self.assertEqual((row['shared_count'],row['union_count']),(23,25))
        self.assertEqual(row['jaccard'],23/25)
        self.assertEqual(row['source']['capture_status'][0]['status'],'partial')
        self.assertEqual(row['source']['source']['relative_file_path'],'etcm_herbs/Beta.json')
        self.assertEqual(data['coverage']['with_targets'],2)
        envelope=invoke({'tool':'herb_target_similarity','arguments':{'name':'Alpha'}},self.record,self.version).model_dump()
        self.assertEqual(envelope['status'],'ok');self.assertEqual(envelope['data']['items'][0]['shared_count'],23)

    def test_zero_record_sources_are_not_scored_as_observed_negatives(self):
        with sqlite3.connect(self.database) as db:db.execute("DELETE FROM edges WHERE herb_id<>? AND kind='target'",(self.record['source_record_id'],))
        data=self.run_query();self.assertEqual(data['coverage']['without_targets'],1)
        self.assertEqual(data['eligible_peers'],0);self.assertEqual(data['items'],[])
        with sqlite3.connect(self.database) as db:db.execute("DELETE FROM edges WHERE kind='target'")
        result=invoke({'tool':'herb_target_similarity','arguments':{'name':'Alpha'}},self.record,self.version)
        self.assertEqual(result.status,'empty');self.assertEqual(result.data.profile.target_count,0)

    def test_snapshot_changes_and_all_candidate_binding_are_checked(self):
        first=self.run_query()
        self.assertEqual(self.run_query(expected_revision=first['catalog_revision'])['items'],first['items'])
        with sqlite3.connect(self.database) as db:db.execute("UPDATE herbs SET file_sha256='changed' WHERE source_record_id<>?",(self.record['source_record_id'],))
        with self.assertRaises(SnapshotChanged):self.run_query()
        result=invoke({'tool':'herb_target_profile','arguments':{'name':'Alpha'}},self.record,self.version)
        self.assertEqual(result.error.code,'snapshot_changed')

    def test_in_place_data_change_invalidates_saved_analysis_revision(self):
        first=self.run_query()
        with sqlite3.connect(self.database) as db:db.execute("DELETE FROM edges WHERE kind='target' AND herb_id<>?",(self.record['source_record_id'],))
        with self.assertRaises(SnapshotChanged):self.run_query(expected_revision=first['catalog_revision'])

    def test_strict_inputs_unknown_tools_and_invalid_outputs_stop(self):
        for call in ({'tool':'shell','arguments':{}},
                     {'tool':'herb_target_similarity','arguments':{'name':'Alpha','top_k':True}},
                     {'tool':'herb_target_profile','arguments':{'name':'Alpha','sql':'SELECT 1'}},
                     {'tool':'herb_target_similarity','arguments':{'name':'Alpha','metric':'treatment_probability'}}):
            with patch('tcm.target_similarity.query',side_effect=AssertionError('Invalid input executed')):
                self.assertEqual(invoke(call,self.record,self.version).error.code,'invalid_arguments')
        data=self.run_query();data['items'][0]['jaccard']=0.99
        with patch('tcm.target_similarity.query',return_value=data):
            self.assertEqual(invoke({'tool':'herb_target_similarity','arguments':{'name':'Alpha'}},self.record,self.version).error.code,'invalid_result')


if __name__=='__main__':unittest.main()
