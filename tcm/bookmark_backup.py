"""Portable bookmark data, without cookies, owner IDs, source text or credentials."""
import hashlib
import json
import re

MAX_BYTES = 2 * 1024 * 1024
FORMAT = 'tcm_research_bookmarks'


class InvalidBookmarkBackup(RuntimeError):
    pass


def require(condition):
    if not condition:
        raise InvalidBookmarkBackup('Invalid bookmark backup')


def text(value, minimum, maximum):
    return isinstance(value, str) and minimum <= len(value) <= maximum


def sha(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def validate_item(item):
    require(isinstance(item, dict) and set(item) == {'title', 'note', 'payload'})
    require(text(item['title'], 1, 120) and item['title'].strip() and text(item['note'], 0, 2000))
    payload = item['payload']
    require(isinstance(payload, dict) and set(payload) == {'kind', 'focus'})
    focus = payload['focus']
    require(isinstance(focus, dict))
    if payload['kind'] == 'research':
        require(set(focus) == {'kind', 'name', 'last_question', 'index_sha256', 'view_sha256',
                               'retriever_sha256', 'guards_sha256'})
        require(focus['kind'] == 'mineru_research_chat_v1')
        require(text(focus['name'], 1, 200) and text(focus['last_question'], 1, 500))
        require(all(sha(focus[k]) for k in ('index_sha256', 'view_sha256', 'retriever_sha256', 'guards_sha256')))
    elif payload['kind'] == 'relation':
        kind = focus.get('query_kind')
        require(kind in ('ingredient', 'target', 'common_target', 'target_ingredient', 'herb_target_paths', 'disease', 'target_herb'))
        keys = {'kind', 'query_kind', 'page', 'dataset_version', 'herb_source_fingerprint', 'herb_sources'}
        if kind in ('target_ingredient', 'target_herb'):keys.add('uniprot')
        if kind in ('target_ingredient', 'target_herb', 'herb_target_paths'):keys.add('target_export_sha256')
        require(set(focus) == keys and focus['kind'] == 'etcm_relation_focus_v1')
        require(type(focus['page']) is int and 1 <= focus['page'] <= 1000)
        require(text(focus['dataset_version'], 1, 200) and sha(focus['herb_source_fingerprint']))
        sources = focus['herb_sources']
        count = 0 if kind in ('target_ingredient', 'target_herb') else 2 if kind == 'common_target' else 1
        require(isinstance(sources, list) and len(sources) == count)
        for source in sources:
            require(isinstance(source, dict) and set(source) == {'source_record_id', 'file_sha256'})
            require(isinstance(source['source_record_id'], str) and
                    re.fullmatch(r'etcm2-local:[0-9a-f]{64}', source['source_record_id']) is not None)
            require(sha(source['file_sha256']))
        require(len({s['source_record_id'] for s in sources}) == count)
        if 'uniprot' in keys:
            require(isinstance(focus['uniprot'], str) and re.fullmatch(r'[A-Z0-9]{6}(?:[A-Z0-9]{4})?', focus['uniprot']) is not None)
        if 'target_export_sha256' in keys:require(sha(focus['target_export_sha256']))
    else:
        raise InvalidBookmarkBackup('Unsupported bookmark kind')
    require(len(canonical(payload)) <= 8192)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def encode(items):
    require(isinstance(items, list) and len(items) <= 100)
    for item in items:validate_item(item)
    body = {'format': FORMAT, 'version': 1, 'items': items}
    result = canonical({**body, 'sha256': hashlib.sha256(canonical(body)).hexdigest()})
    require(len(result) <= MAX_BYTES)
    return result


def decode(raw):
    require(isinstance(raw, bytes) and len(raw) <= MAX_BYTES)
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result)
            result[key] = value
        return result
    def constant(value):
        raise InvalidBookmarkBackup('Non-JSON numeric constant')
    try:
        data = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=pairs, parse_constant=constant)
        require(isinstance(data, dict) and set(data) == {'format', 'version', 'items', 'sha256'})
        require(data['format'] == FORMAT and type(data['version']) is int and data['version'] == 1)
        require(sha(data['sha256']))
        require(isinstance(data['items'], list) and len(data['items']) <= 100)
        for item in data['items']:validate_item(item)
        body = {k: data[k] for k in ('format', 'version', 'items')}
        require(hashlib.sha256(canonical(body)).hexdigest() == data['sha256'])
        return data['items']
    except (UnicodeError, ValueError, TypeError, RecursionError, OverflowError) as exc:
        raise InvalidBookmarkBackup('Invalid bookmark backup') from exc
