"""Literal disease source discovery; no translation, identity merging or model calls."""
from contextlib import closing
import hashlib
import json
import os
import sqlite3
from time import monotonic

from tcm import disease_catalog as catalog
from tcm.target_similarity import file_token,SnapshotChanged


def search(path, name='', *, page=1, expected_revision=None):
    if (not isinstance(name,str) or name!=name.strip() or len(name)>100 or '\n' in name or '\r' in name
            or type(page) is not int or not 1<=page<=10000):
        raise ValueError('Invalid disease search')
    before=file_token(path);con,meta=catalog.connect(path)
    normalized=catalog.key(name);terms=normalized.split()
    # Literal substring predicates: user input cannot introduce SQL wildcards.
    where=' AND '.join('instr(name_key,?)>0' for _ in terms) or ('0=1' if name else '1=1')
    started=monotonic()
    with closing(con):
        con.set_progress_handler(lambda:int(monotonic()-started>5),10000);con.execute('BEGIN')
        total=con.execute('SELECT COUNT(*) FROM diseases WHERE '+where,terms).fetchone()[0]
        rows=con.execute('SELECT record_id,name,name_key,document FROM diseases WHERE '+where+
            ' ORDER BY CASE WHEN name_key=? THEN 0 WHEN substr(name_key,1,length(?))=? THEN 1 ELSE 2 END,name_key,record_id LIMIT 20 OFFSET ?',
            [*terms,normalized,normalized,normalized,(page-1)*20]).fetchall()
        items=[]
        for row in rows:
            p=json.loads(row['document'])
            items.append({'record_id':row['record_id'],'name':row['name'],'target_count':p['target_count'],
                          'capture_state':p['capture_state'],'species_status':p['species_status'],'source':p['source'],
                          'match_kind':'browse' if not normalized else 'exact' if row['name_key']==normalized else
                              'prefix' if row['name_key'].startswith(normalized) else 'keywords'})
    revision=hashlib.sha256(catalog.dumps(['disease_name_search_v1',meta['dataset_version'],meta['source_fingerprint'],before]).encode()).hexdigest()
    if before!=file_token(path) or expected_revision is not None and expected_revision!=revision:
        raise SnapshotChanged('Disease search snapshot changed')
    return {'query':name,'dataset_version':meta['dataset_version'],'catalog_revision':revision,
            'total':total,'page':page,'has_next':page*20<total,'items':items}


def query(call, *, expected_revision=None):
    path=os.getenv('ETCM_DISEASE_DB')
    if not path:raise ValueError('Disease catalog not configured')
    return search(path,call.arguments.name,page=call.arguments.page,expected_revision=expected_revision)


def execute_turn(parsed, *, expected_revision=None):
    from tcm.analysis_tools import invoke,command
    result={'topic':'target_analysis','status':'ok','llm_calls':0,'network_calls':0}
    envelope=invoke(parsed.model_dump(),expected_revision=expected_revision)
    result['analysis_result']=envelope.model_dump()
    if envelope.status=='error':
        result.update(status='unavailable',message='疾病目录已改变，请重新搜索。' if envelope.error.code=='snapshot_changed' else '疾病来源目录暂不可用，请确认已加载索引。')
        return result,None
    data=envelope.data
    result['message']=f'找到{data.total}条疾病来源，当前显示{len(data.items)}条。请选择要查看的来源。' if data.total else '未找到匹配的疾病来源。可尝试较短的英文关键词；当前没有经过核验的中文疾病别名映射。'
    if data.total and not data.items:result['message']=f'共有{data.total}条来源，当前页超出范围，请返回上一页或重新搜索。'
    current=parsed.model_dump();result['replay_query']=command(current)
    return result,{'kind':'etcm_analysis_focus_v1','call':current,'catalog_revision':data.catalog_revision}
