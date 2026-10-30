"""Bounded, deterministic ETCM relationship queries; no model or inferred entity joins."""
from contextlib import closing
import os
import re
import sqlite3

from tcm.etcm_enrichment import connect, relations

LABELS = {'ingredient': '成分', 'target': '靶点', 'common_target': '共同靶点', 'target_ingredient': '靶点成分',
          'herb_target_paths': '成分靶点', 'disease': '疾病富集', 'target_herb': '靶点药材'}
LIMIT = 20


def common_target_sentence(body):
    """Consume a complete two-herb query, including optional context and provenance.

    Extra tasks must not disappear while simplifying an ordinary read-only query.
    Names are resolved against the existing catalog by execute(), not this grammar.
    """
    pair = r'(?P<pair>[^，,；;！？?!\n\r]{1,256}?)'
    goal = r'(?:共同靶点|共同标识)'
    evidence = (r'(?:[，,；;]\s*(?:并|同时)?(?:请)?'
                r'(?:保留|附上|给出|列出|展示)(?:双方|两边|各自)?的?'
                r'(?:原始记录|原始行|出处)(?:(?:的)?位置|和出处|与出处|及出处)?)?')
    patterns = (
        r'(?:请帮我|帮我|麻烦|请)?\s*我(?:正在|在)?(?:整理|比较|核对|研究)\s*'+pair+
        r'[，,]\s*(?:我)?(?:想|需要|希望)(?:查询|查一下|核对|查看|比较|查)\s*(?:两者的|双方的|它们的)?'+goal,
        r'(?:请帮我|帮我|麻烦|请)?\s*(?:查询|查一下|查找|核对|查看|比较|查)?\s*'+pair+
        r'(?:有哪些|有什么|的)?'+goal+r'(?:有哪些|是什么)?',
    )
    for pattern in patterns:
        match = re.fullmatch(pattern+evidence, body)
        if match is None:continue
        entities = re.split(r'\s*(?:和|与|、|\|)\s*', match['pair'].strip())
        if (len(entities)!=2 or any(not re.fullmatch(r'[\u4e00-\u9fffA-Za-z0-9 .():_\-]{1,100}',name)
                                   or re.search(r'我|想|需要|整理|查询|核对|共同|靶点|文献|论文|价格|不要|这些|这两味',name)
                                   for name in entities)):
            continue
        return entities
    return None


def parse(text, *, explicit=False):
    if not isinstance(text, str):
        return None
    text = text.strip()
    if explicit:
        if not re.match(r'^/relations(?:\s|$)', text):
            return None
        body = text[len('/relations'):].strip()
    else:
        body = text
    request = {'topic': 'etcm_relations', 'page': 1}
    if len(body) > 800:
        return {**request, 'error': 'invalid_query'} if explicit else None
    body = body.rstrip('？?。！!').strip()
    if not explicit and body in ('这些成分对应哪些靶点','这些成分关联哪些靶点','它的成分关联哪些靶点','这味药的成分关联哪些靶点'):
        return {'topic':'etcm_relations','followup':True,'action':'paths','page':1}
    if not explicit and body in ('回看关联结果','刚才的关联结果','这些成分的出处','这些路径的出处'):
        return {'topic':'etcm_relations','followup':True,'action':'replay'}
    page = re.search(r'\s+第([0-9]+)页$', body)
    if page:
        request['page'] = int(page.group(1)) if len(page.group(1)) <= 4 else 0
        body = body[:page.start()].strip()
    if not explicit:
        names=common_target_sentence(body)
        if names is not None:
            return {**request,'kind':'common_target','entities':names,
                    'resolve_unique_sources':True,
                    **({'error':'invalid_query'} if not 1<=request['page']<=1000 else {})}
    if explicit:
        match = re.fullmatch(r'(共同靶点|靶点成分|靶点药材|成分靶点|疾病富集|成分|靶点)\s+(.+)', body)
        if not match:
            return {**request, 'error': 'invalid_query'}
        label, names = match.groups()
        entities = [x.strip() for x in names.split('|')]
    else:
        rephrase = re.fullmatch(r'(?:请)?(?:帮我)?把\s*(.{1,256}?)的(?:化学)?成分和(?:相关|关联)?靶点(?:串起来|关联起来|联系起来)', body)
        if rephrase:
            return parse('/relations 成分靶点 '+rephrase.group(1)+' 第'+str(request['page'])+'页',explicit=True)
        body = re.sub(r'^(?:请帮我|帮我|麻烦|请)?\s*(?:查询|查一下|查找|查|比较)?\s*', '', body)
        reverse = re.fullmatch(r'(?:靶点\s*)?([A-Za-z0-9][A-Za-z0-9.-]{0,31})(?:关联哪些药材|关联的药材有哪些|有哪些关联药材|对应哪些药材)', body)
        if reverse:
            return parse('/relations 靶点药材 '+reverse.group(1)+' 第'+str(request['page'])+'页', explicit=True)
        disease = re.fullmatch(r'(.+?)(?:有哪些|有什么|的)(?:来源)?(?:疾病富集(?:记录)?|富集疾病(?:记录)?)(?:有哪些|是什么)?', body)
        if disease:
            return parse('/relations 疾病富集 '+disease.group(1)+' 第'+str(request['page'])+'页', explicit=True)
        paths = re.fullmatch(r'(.+?)的(?:化学)?成分(?:关联哪些靶点|对应哪些靶点|关联的靶点有哪些)', body)
        if paths:
            return parse('/relations 成分靶点 '+paths.group(1)+' 第'+str(request['page'])+'页', explicit=True)
        match = re.fullmatch(r'(.+?)(?:有哪些|有什么|的)(?:共同靶点)(?:有哪些|是什么)?', body)
        if match:
            label = '共同靶点'
            entities = re.split(r'\s*(?:和|与|、|\|)\s*', match.group(1))
        else:
            match = re.fullmatch(r'(.+?)(?:有哪些|有什么|的)(?:关联)?(化学成分|成分|靶点)(?:有哪些|是什么)?', body)
            if not match:
                return None
            name, label = match.groups()
            label = '成分' if label == '化学成分' else label
            if label == '成分':
                explicit_target = re.match(r'^靶点\s*', name)
                token = name[explicit_target.end():].strip() if explicit_target else name.strip()
                from tcm.target_ingredients import recognizes_gene
                if (explicit_target or
                        re.fullmatch(r'[A-Za-z][A-Za-z0-9.-]{0,31}', token) and
                        (any(c.isdigit() for c in token) or recognizes_gene(token))):
                    label, name = '靶点成分', token
            entities = [name.strip()]
    request.update(kind=next(k for k, v in LABELS.items() if v == label), entities=entities)
    if not explicit and label=='共同靶点':request['resolve_unique_sources']=True
    if (not 1 <= request['page'] <= 1000 or len(entities) != (2 if label == '共同靶点' else 1)
            or any(not x or len(x) > 256 or re.search(r'[\n\r]', x) for x in entities)):
        request['error'] = 'invalid_query'
    # Multiple names are not silently treated as a single herb.
    if label != '共同靶点' and re.search(r'[和与、|]', entities[0]):
        request['error'] = 'invalid_query'
    if label in ('靶点成分', '靶点药材') and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,31}', entities[0]):
        request['error'] = 'invalid_query'
    return request


def shared_targets(path, records, version, *, offset=0):
    """Intersect explicit source-declared UniProt groups; names alone never qualify."""
    import hashlib
    import json
    from tcm.target_similarity import file_token
    if type(offset) is not int or offset < 0 or offset % LIMIT:
        raise ValueError('Invalid target page offset')
    before = file_token(path)
    if len(records) != 2 or records[0]['source_record_id'] == records[1]['source_record_id']:
        raise ValueError('Choose two different sources')
    with closing(connect(path)) as con:
        con.execute('BEGIN')
        ids = [r['source_record_id'] for r in records]
        for record in records:
            row = con.execute('SELECT file_sha256 FROM herbs WHERE source_record_id=? AND dataset_version=?',
                              (record['source_record_id'], version)).fetchone()
            if row is None or row[0] != record['source']['file_sha256']:
                raise ValueError('Source snapshot differs')
        where = ("FROM nodes n WHERE n.kind='target' AND n.identifier IS NOT NULL "
                 "AND n.identity_status LIKE 'source_declared_uniprot_not_externally_verified%' "
                 "AND EXISTS(SELECT 1 FROM edges e WHERE e.node_id=n.node_id AND e.herb_id=?) "
                 "AND EXISTS(SELECT 1 FROM edges e WHERE e.node_id=n.node_id AND e.herb_id=?)")
        total = con.execute('SELECT COUNT(*) ' + where, ids).fetchone()[0]
        nodes = con.execute('SELECT n.node_id,n.identifier,n.identity_status ' + where +
                            ' ORDER BY n.identifier,n.node_id LIMIT ? OFFSET ?', [*ids, LIMIT, offset]).fetchall()
        items = []
        import json
        for node in nodes:
            sources = []
            for identifier in ids:
                count = con.execute('SELECT COUNT(*) FROM edges WHERE node_id=? AND herb_id=?',
                                    (node['node_id'], identifier)).fetchone()[0]
                edges = con.execute('SELECT edge_id,source_pointer,raw_row,capture_status FROM edges '
                                    'WHERE node_id=? AND herb_id=? ORDER BY source_pointer,edge_id LIMIT 3',
                                    (node['node_id'], identifier)).fetchall()
                sources.append({'source_record_id': identifier, 'source_row_count': count,
                                'witnesses_truncated': count > 3,
                                'witnesses': [{**dict(e), 'raw_row': json.loads(e['raw_row']),
                                               'capture_status': json.loads(e['capture_status'])} for e in edges]})
            items.append({**dict(node), 'sources': sources})
        if before != file_token(path):
            raise ValueError('Source snapshot changed during query')
        revision = hashlib.sha256(json.dumps([version, before], sort_keys=True).encode()).hexdigest()
        return {'catalog_revision': revision, 'total': total, 'offset': offset, 'limit': LIMIT, 'returned_count': len(items),
                'next_offset': offset + LIMIT if offset + LIMIT < total else None,
                'count_unit': 'distinct_source_declared_uniprot', 'items': items}


def execute(request):
    from tcm.chat import execute_request
    result = {'topic': 'etcm_relations', 'status': 'ok', 'llm_calls': 0, 'network_calls': 0}
    if request.get('error') or request.get('kind') not in LABELS:
        return {**result, 'status': 'clarification_needed',
                'message': '可查询一味药的成分或靶点、两味药的共同靶点，或按基因符号/UniProt查询靶点成分，如“CYP24A1有哪些成分”。每次靶点成分查询只用一个明确标识，不猜蛋白别名；分页范围为1至1000页。'}
    if request['kind'] == 'target_ingredient':
        from tcm.target_ingredients import execute as execute_target_ingredients
        return execute_target_ingredients(request)
    if request['kind'] == 'target_herb':
        from tcm.target_herbs import execute as execute_target_herbs
        return execute_target_herbs(request)
    path = os.getenv('ETCM_ENRICHMENT_DB')
    if not path:
        return {**result, 'status': 'unavailable', 'message': '本地ETCM关联目录未启用，不改用模型或联网补答。'}
    groups, selected = [], []
    unavailable = False
    for name in request['entities']:
        record = execute_request({'source_record_id': name} if name.startswith('etcm2-local:') else {'name': name})
        unavailable |= record['status'] in ('database_unavailable', 'dataset_not_found')
        candidate_names={c['name_zh'] for r in record['records'] for c in r.get('name_zh_candidates',[]) if c.get('name_zh')}
        display_name=(next(iter(candidate_names)) if len(candidate_names)==1 else record['records'][0]['fields']['name_pinyin']['value']) if name.startswith('etcm2-local:') and len(record['records'])==1 else name
        groups.append({'query': name, 'display_name':display_name,'resolution_status': record['status'],
                       'options': [{'source_record_id': r['source_record_id'],
                                    'name': r['fields']['name_pinyin']['value'],
                                    'medicinal_part': r['fields']['medicinal_part']['value'],
                                    'source': r['source']} for r in record['records']]})
        if (request.get('resolve_unique_sources') is True and request['kind']=='common_target'
                and record['status']=='name_mapping_candidates'
                and record['total_record_count']==1 and len(record['records'])==1 and not record['truncated']):
            groups[-1]['selection_method']='unique_local_candidate'
        selected.append(record)
    payload = {'kind': request['kind'], 'page': request['page'], 'groups': groups,
               'ingredient_detail_available': bool(os.getenv('ETCM_SOURCE_ROOT'))}
    result['relations'] = payload
    if unavailable:
        return {**result, 'status': 'unavailable', 'message': '当前ETCM来源数据不可用，未执行关联查询。'}
    if any(r['status']!='ok' and g.get('selection_method')!='unique_local_candidate'
           for r,g in zip(selected,groups)):
        return {**result, 'status': 'clarification_needed',
                'message': '请核对并选择本次查询的ETCM来源。中文名称是候选，未自动确认身份；没有匹配项时请改用已收录拼音或来源ID。'}
    records = [r['records'][0] for r in selected]
    version = selected[0]['dataset_version']
    if any(r['dataset_version'] != version for r in selected):
        return {**result, 'status': 'unavailable', 'message': '本次来源版本不一致，请重新选择。'}
    if len(records) == 2 and records[0]['source_record_id'] == records[1]['source_record_id']:
        return {**result, 'status': 'clarification_needed', 'message': '共同靶点查询需要两条不同的ETCM来源。'}
    try:
        offset = (request['page'] - 1) * LIMIT
        if request['kind'] == 'herb_target_paths':
            from tcm.herb_target_paths import query as query_paths
            if not os.getenv('ETCM_TARGET_DB'):
                return {**result, 'status': 'unavailable', 'message': '本地靶点成分目录未启用，无法查询候选路径，不改用模型补答。'}
            data = query_paths(path, os.environ['ETCM_TARGET_DB'], records[0], version, page=request['page'])
        else:
            data = (shared_targets(path, records, version, offset=offset) if request['kind'] == 'common_target'
                    else relations(path, records[0], version, kind=request['kind'], limit=LIMIT, offset=offset))
        payload.update(data=data, dataset_version=version)
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        return {**result, 'status': 'unavailable', 'message': '关联目录不可用、未完成或与来源快照不一致，未改用模型补答。'}
    if request['kind'] == 'herb_target_paths':
        result['message']=f"{groups[0]['display_name']}：{data['total']}条成分—靶点候选路径。"
        result['notes']=['仅连接成分名称、分子式和分子量一致的来源行；未验证化学身份、结合或机制。',
                         '未匹配不表示无关联，覆盖及字段缺失见详情。']
        return result
    if request['kind'] == 'disease':
        payload['source_capture_status'] = data.get('source_capture_status', [])
        result['message']=f"{groups[0]['display_name']}：{data['total']}条疾病富集记录。"
        result['notes']=['富集关联不是适应证或治疗推荐，不证明药材可治疗所列疾病。',
                         '保留来源原名；未评定统计显著性，抓取完整性见原始证据。']
        if not data['total']:result['message']+='当前来源未记录，不等于不存在。'
        return result
    name='与'.join(g['display_name'] for g in groups)
    label={'common_target':'个共同靶点（按UniProt匹配）','ingredient':'条成分记录','target':'条靶点记录'}[request['kind']]
    result['message']=f"{name}：{data['total']}{label}。"
    result['notes']=['ETCM来源关联不证明机制或药效；中文名候选的药材身份尚未核验。',
                     '空结果仅表示当前来源未记录，不表示不存在；抓取完整性未保证。']
    if any(g.get('selection_method')=='unique_local_candidate' for g in groups):
        result['notes'].insert(0,'按本地目录唯一候选来源自动查询，实际来源及文件哈希见详情。')
    if not data['total']:result['message']+='当前来源未记录，不表示不存在。'
    return result
