"""Bounded PubMed ESearch/EFetch adapter; no generated medical conclusions."""
from datetime import datetime, timezone
import hashlib
import json
import socket
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from tcm.literature_evidence import annotate_article, annotate_batch, normalize_doi

BASE = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/'
MAX_BYTES = 4 * 1024 * 1024
_LOCK = threading.Lock()
_LAST_REQUEST = 0.0


class PubMedError(Exception):
    """Safe public error code, without URLs, credentials or provider bodies."""


def _download(endpoint, params):
    # Shared across clients in this process; multi-process deployments need a
    # shared rate limiter because NCBI's quota applies to the public IP.
    global _LAST_REQUEST
    with _LOCK:
        time.sleep(max(0, .4 - (time.monotonic() - _LAST_REQUEST)))
        _LAST_REQUEST = time.monotonic()
        request = Request(BASE + endpoint + '?' + urlencode(params),
                          headers={'User-Agent': 'tcm-research-assistant/1.0'})
        with urlopen(request, timeout=15) as response:
            body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise PubMedError('response_too_large')
    return body


def _text(node):
    return ''.join(node.itertext()).strip() if node is not None else ''


def _article(node):
    citation = node.find('MedlineCitation')
    article = citation.find('Article') if citation is not None else None
    pmid = _text(citation.find('PMID')) if citation is not None else ''
    if article is None or not pmid.isascii() or not pmid.isdecimal():
        raise PubMedError('invalid_response')
    identifiers = {item.get('IdType'): _text(item)
                   for item in node.findall('PubmedData/ArticleIdList/ArticleId')}
    abstract = [{'label': item.get('Label', ''), 'text': _text(item)}
                for item in article.findall('Abstract/AbstractText') if _text(item)]
    title = _text(article.find('ArticleTitle'))
    if not title:
        raise PubMedError('invalid_response')
    doi = identifiers.get('doi')
    if not doi:
        doi = next((_text(item) for item in article.findall('ELocationID')
                    if item.get('EIdType') == 'doi'), None)
    relations = []
    for relation in citation.findall('CommentsCorrectionsList/CommentsCorrections'):
        linked_pmid = _text(relation.find('PMID'))
        relations.append({'ref_type': relation.get('RefType', ''),
                          'pmid': linked_pmid if linked_pmid.isascii() and linked_pmid.isdecimal() else None,
                          'raw_pmid': linked_pmid,
                          'pmid_version': relation.find('PMID').get('Version') if relation.find('PMID') is not None else None,
                          'ref_source': _text(relation.find('RefSource')), 'note': _text(relation.find('Note'))})
    candidates = list(dict.fromkeys([_text(item) for item in node.findall('PubmedData/ArticleIdList/ArticleId')
                                     if item.get('IdType') == 'doi'] +
                                    [_text(item) for item in article.findall('ELocationID') if item.get('EIdType') == 'doi']))
    invalid_dois = [_text(item) for item in article.findall('ELocationID')
                    if item.get('EIdType') == 'doi' and item.get('ValidYN') == 'N']
    invalid_keys = {normalize_doi(value) for value in invalid_dois} - {None}
    return annotate_article({
        'pmid': pmid, 'doi': doi, 'pmc_id': identifiers.get('pmc'),
        'title': title, 'journal': _text(article.find('Journal/Title')),
        'publication_date': ' '.join(_text(item) for item in
                                    article.findall('Journal/JournalIssue/PubDate/*')),
        'publication_types': [_text(item) for item in article.findall('PublicationTypeList/PublicationType')],
        'abstract': abstract, 'evidence_scope': 'abstract' if abstract else 'metadata_only',
        'full_text_retrieved': False, 'url': f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/',
        'doi_candidates': candidates, 'relations': relations,
        'doi_match_candidates': [value for value in candidates
                                 if value not in invalid_dois and normalize_doi(value) not in invalid_keys],
        'source_invalid_dois': invalid_dois,
        'pmid_version': citation.find('PMID').get('Version'),
        'mesh_descriptors': [_text(item) for item in citation.findall('MeshHeadingList/MeshHeading/DescriptorName')],
        'publication_status': _text(node.find('PubmedData/PublicationStatus')),
    })


class PubMedClient:
    def __init__(self, transport=None):
        self.transport = transport or _download

    def search(self, query, limit=5):
        if not isinstance(query, str) or not query.strip() or len(query) > 2000:
            raise ValueError('query must contain 1–2000 characters')
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError('limit must be an integer from 1 to 20')
        started = time.monotonic()
        result = {'provider': 'NCBI PubMed E-utilities', 'query': query,
                  'retrieved_at': datetime.now(timezone.utc).isoformat(), 'limit': limit,
                  'sort': 'relevance', 'status': 'error', 'total_count': None,
                  'query_translation': None, 'warnings': {}, 'requested_pmids': [],
                  'missing_pmids': [], 'articles': [], 'response_sha256': {},
                  'disclaimer_url': 'https://www.ncbi.nlm.nih.gov/About/disclaimer.html'}

        def request(endpoint, params):
            body = self.transport(endpoint, dict(db='pubmed', tool='tcm_research_assistant', **params))
            if not isinstance(body, bytes) or len(body) > MAX_BYTES:
                raise PubMedError('invalid_response')
            result['response_sha256'][endpoint] = hashlib.sha256(body).hexdigest()
            return body

        try:
            data = json.loads(request('esearch.fcgi', {'term': query, 'retmode': 'json',
                                                       'retmax': limit, 'sort': 'relevance'}))
            if not isinstance(data, dict) or data.get('error'):
                raise PubMedError('provider_error')
            search = data['esearchresult']
            if search.get('ERROR') or search.get('errorlist'):
                raise PubMedError('query_rejected')
            count = int(search['count'])
            ids = search['idlist']
            if (count < 0 or not isinstance(ids, list) or len(ids) > limit or
                    any(not isinstance(i, str) or not i.isascii() or not i.isdecimal() for i in ids) or
                    len(set(ids)) != len(ids) or len(ids) > count or (count > 0 and not ids)):
                raise PubMedError('invalid_response')
            result.update(total_count=count, query_translation=search.get('querytranslation'),
                          warnings=search.get('warninglist', {}), requested_pmids=ids)
            if not ids:
                result['status'] = 'no_results'
                return result
            root = ET.fromstring(request('efetch.fcgi', {'id': ','.join(ids), 'retmode': 'xml'}))
            if root.tag != 'PubmedArticleSet' or root.find('.//ERROR') is not None:
                raise PubMedError('invalid_response')
            articles = [_article(node) for node in root.findall('PubmedArticle')]
            by_id = {article['pmid']: article for article in articles}
            if len(by_id) != len(articles) or set(by_id) - set(ids):
                raise PubMedError('invalid_response')
            result['articles'] = [by_id[i] for i in ids if i in by_id]
            result['missing_pmids'] = [i for i in ids if i not in by_id]
            result['status'] = 'partial_fetch' if result['missing_pmids'] else 'ok'
        except HTTPError as exc:
            result['error_code'] = 'rate_limited' if exc.code == 429 else 'http_error'
        except (TimeoutError, socket.timeout):
            result['error_code'] = 'timeout'
        except URLError as exc:
            result['error_code'] = 'timeout' if isinstance(exc.reason, TimeoutError) else 'network_error'
        except OSError:
            result['error_code'] = 'network_error'
        except PubMedError as exc:
            result['error_code'] = str(exc)
        except (ValueError, KeyError, TypeError, AttributeError, ET.ParseError):
            result['error_code'] = 'invalid_response'
        finally:
            result['duration_ms'] = round((time.monotonic() - started) * 1000, 2)
            result['completed_at'] = datetime.now(timezone.utc).isoformat()
        return annotate_batch(result)
