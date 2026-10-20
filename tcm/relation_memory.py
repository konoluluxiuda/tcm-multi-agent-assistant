"""Small durable relation focus; raw evidence stays in versioned read-only catalogs."""
from contextlib import closing
import os
import re
import sqlite3
import json

from tcm.etcm_enrichment import connect


def context_from(snapshot):
    return snapshot.get('relation') if isinstance(snapshot,dict) and snapshot.get('kind')=='agent_contexts_v1' else None


def metadata():
    with closing(connect(os.environ['ETCM_ENRICHMENT_DB'])) as con:
        return {r['key']:json.loads(r['value']) for r in con.execute('SELECT key,value FROM metadata')}


def target_fingerprint():
    from tcm.target_ingredients import connect as target_connect
    con,meta=target_connect(os.environ['ETCM_TARGET_DB']);con.close()
    return meta['export_sha256']


def capture(result):
    if result.get('status')!='ok' or 'data' not in result.get('relations',{}):return None
    payload=result['relations'];meta=metadata()
    focus={'kind':'etcm_relation_focus_v1','query_kind':payload['kind'],'page':payload['page'],
           'dataset_version':payload['dataset_version'],'herb_source_fingerprint':meta['source_fingerprint'],
           'herb_sources':[]}
    if payload['kind']=='target_ingredient':
        focus['uniprot']=payload['target']['uniprot']
    else:
        for group in payload['groups']:
            record=group['options'][0]
            focus['herb_sources'].append({'source_record_id':record['source_record_id'],'file_sha256':record['source']['file_sha256']})
    if payload['kind'] in ('target_ingredient','herb_target_paths'):focus['target_export_sha256']=target_fingerprint()
    return focus


def valid(focus):
    from tcm.chat import execute_request
    from tcm.relation_queries import LABELS
    if (not isinstance(focus,dict) or focus.get('kind')!='etcm_relation_focus_v1' or focus.get('query_kind') not in LABELS
            or type(focus.get('page')) is not int or not 1<=focus['page']<=1000):return False
    meta=metadata()
    if focus.get('dataset_version')!=meta['dataset_version'] or focus.get('herb_source_fingerprint')!=meta['source_fingerprint']:return False
    if os.getenv('TCM_DATASET_VERSION') and os.environ['TCM_DATASET_VERSION']!=focus['dataset_version']:return False
    sources=focus.get('herb_sources')
    expected=0 if focus['query_kind']=='target_ingredient' else 2 if focus['query_kind']=='common_target' else 1
    if not isinstance(sources,list) or len(sources)!=expected:return False
    if expected==0 and not re.fullmatch(r'[A-Z0-9]{6}(?:[A-Z0-9]{4})?',focus.get('uniprot','')):return False
    for source in sources:
        if not isinstance(source,dict) or not re.fullmatch(r'etcm2-local:[0-9a-f]{64}',source.get('source_record_id','')):return False
        current=execute_request({'source_record_id':source['source_record_id']})
        if (current['status']!='ok' or current['dataset_version']!=focus['dataset_version'] or
                current['records'][0]['source']['file_sha256']!=source.get('file_sha256')):return False
    if focus['query_kind'] in ('target_ingredient','herb_target_paths'):
        if focus.get('target_export_sha256')!=target_fingerprint():return False
    return True


def clarification(message):
    return {'topic':'etcm_relations','status':'clarification_needed','message':message,'llm_calls':0,'network_calls':0}


def execute_turn(request,focus):
    from tcm.relation_queries import execute
    followup=request.get('followup',False)
    try:
        if followup:
            if focus is None:return clarification('当前会话没有已选的关联来源。请先明确药名并选择ETCM来源，再继续追问。'),None
            if not valid(focus):return clarification('上次关联来源、数据版本或目录已改变，请重新查询并选择来源。'),None
            action=request.get('action')
            if action=='paths':
                if len(focus['herb_sources'])!=1:
                    return clarification('上次查询未唯一选中一味药材，请明确药名；不从双药或靶点查询中自动挑选药材。'),None
                request={'kind':'herb_target_paths','entities':[focus['herb_sources'][0]['source_record_id']],'page':1}
            elif action=='replay':
                request={'kind':focus['query_kind'],'entities':[s['source_record_id'] for s in focus['herb_sources']] or [focus['uniprot']],
                         'page':focus['page']}
            else:return clarification('请明确要继续查询的关联类型。'),None
        result=execute(request)
        new_focus=capture(result)
        if followup and result.get('status')=='ok':
            prefix=('沿用本会话已选药材来源，查询全部成分的候选路径，不只限于上一页。' if action=='paths' else
                    '按本会话已选来源和页码重新读取本地资料。')
            result['message']=prefix+'\n'+result['message']
        return result,new_focus
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):
        return {'topic':'etcm_relations','status':'unavailable','message':'关联来源或记忆绑定暂不可用，请核对数据后重新查询。',
                'llm_calls':0,'network_calls':0},None
