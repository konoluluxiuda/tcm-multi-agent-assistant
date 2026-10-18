"""Source relationship direction, conservative identity and transactional upgrades."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tcm.literature_evidence import annotate_article, normalize_doi
from tcm.literature_store import LiteratureStore
from tests.test_pubmed import article, client


def parsed(relations='', publication_type='Review', pmid='123', doi='10.0000/fixture'):
    xml=article(pmid).replace('10.0000/fixture',doi).replace('<PublicationType>Review</PublicationType>',
        '<PublicationType>'+publication_type+'</PublicationType>')
    xml=xml.replace('</MedlineCitation>',relations+'</MedlineCitation>')
    return client(ids=[pmid],xml='<PubmedArticleSet>'+xml+'</PubmedArticleSet>').search('fixture')


def relation(kind, pmid='456'):
    return ('<CommentsCorrectionsList><CommentsCorrections RefType="'+kind+'">'
            '<RefSource>Fixture source</RefSource><PMID>'+pmid+'</PMID><Note>Fixture note</Note>'
            '</CommentsCorrections></CommentsCorrectionsList>')


class EvidenceTests(unittest.TestCase):
    def test_retracted_article_is_distinct_from_retraction_notice(self):
        old=parsed(relation('RetractionIn'))['articles'][0]
        notice=parsed(relation('RetractionOf'),publication_type='Retraction Notice')['articles'][0]
        self.assertEqual(old['assessment']['flags'],['retracted'])
        self.assertEqual(notice['assessment']['flags'],['retraction_notice'])
        self.assertEqual(old['relations'][0]['pmid'],'456')

    def test_errata_concern_and_versions_preserve_direction(self):
        for kind, flag in [('ErratumIn','has_erratum'),('ErratumFor','erratum_notice'),
                           ('ExpressionOfConcernIn','has_expression_of_concern'),
                           ('ExpressionOfConcernFor','expression_of_concern_notice'),
                           ('CorrectedandRepublishedIn','corrected_and_republished_in'),
                           ('CorrectedandRepublishedFrom','corrected_and_republished_from'),
                           ('RetractedandRepublishedFrom','retracted_and_republished_from'),
                           ('UpdateOf','update_of'),('UpdateIn','updated_in')]:
            with self.subTest(kind=kind):
                item=parsed(relation(kind))['articles'][0]
                self.assertEqual(item['assessment']['flags'],[flag])

    def test_unknown_relations_and_missing_pmid_are_retained_not_invented(self):
        item=parsed(relation('FutureRelation','not-a-pmid'))['articles'][0]
        self.assertEqual(item['relations'][0]['ref_type'],'FutureRelation')
        self.assertIsNone(item['relations'][0]['pmid'])
        self.assertEqual(item['assessment']['flags'],[])

    def test_no_title_or_abstract_guessing_no_quality_rating(self):
        item=parsed()['articles'][0]
        item['title']='Retracted randomized trial in humans and mice: meta-analysis'
        item['publication_types']=['Journal Article']
        assessed=annotate_article(item)['assessment']
        self.assertEqual(assessed['study_types'],[])
        self.assertEqual(assessed['population_indexing'],[])
        self.assertEqual(assessed['flags'],[])
        self.assertFalse(assessed['quality_appraised'])

    def test_source_publication_type_and_population_do_not_imply_efficacy(self):
        item=parsed(publication_type='Randomized Controlled Trial')['articles'][0]
        item['mesh_descriptors']=['Humans','Animals']
        assessment=annotate_article(item)['assessment']
        self.assertEqual(assessment['study_types'],['随机对照试验'])
        self.assertEqual(assessment['population_indexing'],['Humans','Animals'])
        self.assertFalse(assessment['quality_appraised'])

    def test_doi_normalization_is_bounded_and_preserves_suffix_punctuation(self):
        self.assertEqual(normalize_doi(' HTTPS://DX.DOI.ORG/10.1234/AbC. '),'10.1234/abc.')
        self.assertEqual(normalize_doi('doi: 10.1234/ABC'),'10.1234/abc')
        self.assertNotEqual(normalize_doi('10.1234/a.'),normalize_doi('10.1234/a'))
        self.assertNotEqual(normalize_doi('10.1234/Á.ABC'),normalize_doi('10.1234/á.abc'))
        for value in (None,'javascript:alert(1)','10.1234/a b','https://other.test/10.1234/a'):
            self.assertIsNone(normalize_doi(value))

    def test_conflicting_dois_are_flagged_and_excluded_from_matching(self):
        xml=article().replace('</Article>', '<ELocationID EIdType="doi">10.9999/different</ELocationID></Article>')
        item=client(xml='<PubmedArticleSet>'+xml+'</PubmedArticleSet>').search('x')['articles'][0]
        self.assertIn('doi_identifier_conflict',item['assessment']['flags'])
        self.assertIsNone(item['identity']['doi_key'])

    def test_same_doi_distinct_pmids_remain_two_records(self):
        result=client(ids=['123','456'],xml='<PubmedArticleSet>'+article()+article('456')+'</PubmedArticleSet>').search('x')
        self.assertEqual(len(result['articles']),2)
        self.assertEqual(result['articles'][0]['identity']['same_doi_other_pmids'],['456'])
        self.assertFalse(result['articles'][0]['identity']['automatically_merged'])

    def test_doi_marked_invalid_by_source_is_not_a_match_key(self):
        xml=article().replace('<ArticleId IdType="doi">10.0000/fixture</ArticleId>','')
        xml=xml.replace('</Article>','<ELocationID EIdType="doi" ValidYN="N">10.9999/invalid</ELocationID></Article>')
        item=client(xml='<PubmedArticleSet>'+xml+'</PubmedArticleSet>').search('x')['articles'][0]
        self.assertEqual(item['doi'],'10.9999/invalid')
        self.assertIn('doi_marked_invalid_by_source',item['assessment']['flags'])
        self.assertIsNone(item['identity']['doi_key'])
        # The same source-invalid DOI is not rescued by a case variant in another field.
        xml=xml.replace('<ArticleIdList>','<ArticleIdList><ArticleId IdType="doi">10.9999/INVALID</ArticleId>')
        item=client(xml='<PubmedArticleSet>'+xml+'</PubmedArticleSet>').search('x')['articles'][0]
        self.assertIsNone(item['identity']['doi_key'])


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'literature.sqlite3'

    def open(self):
        store=LiteratureStore(self.path); self.addCleanup(store.close); return store

    def legacy(self):
        store=self.open()
        result=parsed()
        result.pop('identity_policy',None)
        for key in ('identity','assessment','relations','doi_candidates','mesh_descriptors','publication_status'):
            result['articles'][0].pop(key,None)
        run='legacy-run'
        with store.db:
            body=json.dumps(result)
            payload=json.dumps(result['articles'][0])
            store.db.execute('INSERT INTO search_runs VALUES(?,?,?,?,?)',(run,'fixture',result['retrieved_at'],'ok',body))
            store.db.execute('INSERT INTO articles(pmid,doi,payload) VALUES(?,?,?)',('123','10.0000/fixture',payload))
            store.db.execute('INSERT INTO run_articles VALUES(?,?,?,?)',(run,'123',1,payload))
            store.db.execute('DROP INDEX articles_doi_key')
            store.db.execute('ALTER TABLE articles DROP COLUMN doi_key')
            store.db.execute('PRAGMA user_version=1')
        return store,result,run

    def test_cross_batch_matches_and_doi_changes_do_not_rewrite_history(self):
        store=self.open()
        first=store.save(parsed())
        frozen=store.get(first)
        second=store.save(parsed(pmid='456',doi='HTTPS://DOI.ORG/10.0000/FIXTURE'))
        item=store.get(second)['articles'][0]
        self.assertEqual(item['identity']['same_doi_other_pmids'],['123'])
        self.assertEqual(store.get(first),frozen)
        changed=store.save(parsed(pmid='123',doi='10.9999/new'))
        self.assertEqual(store.get(changed)['articles'][0]['identity']['alerts'],['doi_changed_for_pmid'])
        self.assertEqual(store.get(first),frozen)
        self.assertEqual(store.db.execute('SELECT count(*) FROM articles').fetchone()[0],2)

    def test_same_title_and_different_doi_do_not_merge(self):
        store=self.open(); store.save(parsed())
        run=store.save(parsed(pmid='456',doi='10.9999/new'))
        self.assertEqual(store.get(run)['articles'][0]['identity']['same_doi_other_pmids'],[])

    def test_pmid_version_change_is_not_hidden_by_same_doi(self):
        store=self.open()
        first=parsed(); first['articles'][0]['pmid_version']='1'
        old=store.save(first)
        next_result=deepcopy(first); next_result['articles'][0]['pmid_version']='2'
        new=store.save(next_result)
        self.assertEqual(store.get(new)['articles'][0]['identity']['alerts'],['pmid_version_changed'])
        self.assertEqual(store.get(old)['articles'][0]['pmid_version'],'1')

    def test_upgrade_backs_up_v1_and_preserves_old_batch_exactly(self):
        old,frozen,run=self.legacy()
        store=self.open()
        self.assertEqual(store.db.execute('PRAGMA user_version').fetchone()[0],2)
        self.assertEqual(store.get(run),frozen)
        self.assertTrue(store.migration_backup.is_file())
        self.assertEqual(store.migration_backup.stat().st_mode & 0o777,0o600)
        with sqlite3.connect(store.migration_backup) as backup:
            self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0],1)
            self.assertEqual(json.loads(backup.execute('SELECT payload FROM search_runs').fetchone()[0]),frozen)
        plan=store.db.execute('EXPLAIN QUERY PLAN SELECT payload FROM articles WHERE doi_key=?',('10.0000/fixture',)).fetchall()
        self.assertIn('articles_doi_key',str(plan))
        next_run=store.save(parsed(pmid='456'))
        self.assertEqual(store.get(next_run)['articles'][0]['identity']['same_doi_other_pmids'],['123'])
        self.assertIsNone(self.open().migration_backup)

    def test_failed_upgrade_rolls_back_ddl_and_version_keeps_backup(self):
        old,_,_=self.legacy()
        with patch('tcm.literature_store.doi_key',side_effect=ValueError('fixture backfill failure')):
            with self.assertRaises(ValueError): LiteratureStore(self.path)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1)
            self.assertNotIn('doi_key',[r[1] for r in db.execute('PRAGMA table_info(articles)')])
        self.assertEqual(len(list((self.path.parent/'migration_backups').glob('*.sqlite3'))),1)

    def test_backup_failure_stops_upgrade_before_any_schema_change(self):
        old,_,_=self.legacy()
        with patch('tcm.literature_store.os.fsync',side_effect=OSError('fixture disk failure')):
            with self.assertRaises(OSError): LiteratureStore(self.path)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1)
            self.assertNotIn('doi_key',[r[1] for r in db.execute('PRAGMA table_info(articles)')])


if __name__ == '__main__': unittest.main()
