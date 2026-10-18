"""Conservative, versioned annotations of source metadata; no quality scoring."""
from copy import deepcopy
import re

VERSION = 'pubmed_metadata_v1'
TYPE_LABELS = {
    'Systematic Review': '系统综述', 'Meta-Analysis': 'Meta分析',
    'Randomized Controlled Trial': '随机对照试验', 'Clinical Trial': '临床试验',
    'Controlled Clinical Trial': '对照临床试验', 'Observational Study': '观察性研究',
    'Case Reports': '病例报告', 'Review': '综述', 'Clinical Trial Protocol': '试验方案',
    'Preprint': '预印本', 'Editorial': '社论', 'Comment': '评论',
    'Randomized Controlled Trial, Veterinary': '兽医随机对照试验',
}
RELATION_FLAGS = {
    'RetractionIn': 'retracted', 'RetractionOf': 'retraction_notice',
    'ErratumIn': 'has_erratum', 'ErratumFor': 'erratum_notice',
    'ExpressionOfConcernIn': 'has_expression_of_concern',
    'ExpressionOfConcernFor': 'expression_of_concern_notice',
    'CorrectedandRepublishedIn': 'corrected_and_republished_in',
    'CorrectedandRepublishedFrom': 'corrected_and_republished_from',
    'RetractedandRepublishedIn': 'retracted_and_republished_in',
    'RetractedandRepublishedFrom': 'retracted_and_republished_from',
    'UpdateIn': 'updated_in', 'UpdateOf': 'update_of',
    'RepublishedIn': 'republished_in', 'RepublishedFrom': 'republished_from',
}
NOTICE_TYPES = {'Retracted Publication': 'retracted', 'Retraction of Publication': 'retraction_notice',
                'Retraction Notice': 'retraction_notice', 'Published Erratum': 'erratum_notice',
                'Expression of Concern': 'expression_of_concern_notice',
                'Duplicate Publication': 'duplicate_publication_source_label',
                'Corrected and Republished Article': 'corrected_republication_source_label'}


def normalize_doi(value):
    if not isinstance(value, str): return None
    value = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', value.strip(), flags=re.I)
    value = re.sub(r'^doi:\s*', '', value, flags=re.I).strip()
    # DOI equivalence folds ASCII A-Z only; Unicode lower()/casefold() can
    # incorrectly conflate distinct identifiers (DOI Handbook section 3.3.4).
    ascii_fold = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')
    return value.translate(ascii_fold) if re.fullmatch(r'10\.[0-9]{4,9}/[^\s\x00-\x1f\x7f]+', value) else None


def doi_key(article):
    candidates = (article['doi_match_candidates'] if 'doi_match_candidates' in article else
                  article.get('doi_candidates') or ([article.get('doi')] if article.get('doi') else []))
    keys = {normalize_doi(value) for value in candidates}
    keys.discard(None)
    return next(iter(keys)) if len(keys) == 1 else None


def annotate_article(article):
    record = deepcopy(article)
    types = set(record.get('publication_types', []))
    flags = {NOTICE_TYPES[t] for t in types if t in NOTICE_TYPES}
    flags.update(RELATION_FLAGS[r['ref_type']] for r in record.get('relations', []) if r['ref_type'] in RELATION_FLAGS)
    candidates = record.get('doi_candidates', [])
    eligible = record.get('doi_match_candidates', candidates)
    valid_keys = {normalize_doi(value) for value in eligible}; valid_keys.discard(None)
    if len(valid_keys) > 1: flags.add('doi_identifier_conflict')
    if any(normalize_doi(value) is None for value in candidates): flags.add('unrecognized_doi')
    if record.get('source_invalid_dois'): flags.add('doi_marked_invalid_by_source')
    record['assessment'] = {
        'version': VERSION, 'basis': 'PubMed source metadata only',
        'study_types': [TYPE_LABELS[t] for t in TYPE_LABELS if t in types],
        'population_indexing': [t for t in ('Humans', 'Animals') if t in record.get('mesh_descriptors', [])],
        'flags': sorted(flags), 'quality_appraised': False,
        'relations_checked_at_retrieval': True,
    }
    return record


def annotate_batch(result, catalog=(), previous=()):
    """Only record identity candidates. Never collapse PMIDs or rewrite history."""
    result = deepcopy(result)
    by_key = {}
    for item in [*catalog, *result['articles']]:
        key = doi_key(item)
        if key: by_key.setdefault(key, set()).add(item['pmid'])
    old = {item['pmid']: item for item in previous}
    for item in result['articles']:
        key = doi_key(item)
        item['identity'] = {'doi_key': key,
                            'same_doi_other_pmids': sorted(by_key.get(key, set()) - {item['pmid']}),
                            'automatically_merged': False, 'alerts': []}
        prior = old.get(item['pmid'])
        if prior and key and doi_key(prior) and key != doi_key(prior):
            item['identity'].update(alerts=['doi_changed_for_pmid'], previous_doi=prior.get('doi'))
        if prior and item.get('pmid_version') and prior.get('pmid_version') and item['pmid_version'] != prior['pmid_version']:
            item['identity']['alerts'].append('pmid_version_changed')
            item['identity']['previous_pmid_version'] = prior['pmid_version']
    result['identity_policy'] = 'PMID unique; DOI matches are review candidates, not independent studies or automatic merges'
    return result
