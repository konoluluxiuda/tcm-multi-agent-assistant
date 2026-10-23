"""Disease source queries and deterministic herb ranking, with version-bound replay."""
from contextlib import closing
import hashlib
import json
import os
import sqlite3
from time import monotonic

from tcm import disease_catalog as catalog
from tcm import target_similarity as similarity


def query(call, *, expected_revision=None):
    path=os.getenv('ETCM_DISEASE_DB')
    if not path:raise ValueError('Disease catalog not configured')
    before=similarity.file_token(path)
    found=catalog.lookup(path,call.arguments.name,page=getattr(call.arguments,'page',1))
    if found['resolution_status']!='source_record':raise ValueError('Resolve one disease source first')
    con,meta=catalog.connect(path)
    with closing(con):
        started=monotonic();con.set_progress_handler(lambda:int(monotonic()-started>10),10000)
        con.execute('BEGIN')
        if found['source_fingerprint']!=meta['source_fingerprint']:raise similarity.SnapshotChanged('Disease catalog changed')
        identical=con.execute("SELECT COUNT(*) FROM diseases WHERE json_extract(document,'$.target_set_sha256')=? AND json_extract(document,'$.target_count')>0",(found['profile']['target_set_sha256'],)).fetchone()[0]
    data={k:found[k] for k in ('record_id','dataset_version','profile','target_ids','rows','page','has_next')}
    data['identical_set_sources']=identical
    herb_revision=None
    if call.tool=='rank_herbs_for_gene_set':
        path_herb=os.getenv('TCM_DB');path_side=os.getenv('ETCM_ENRICHMENT_DB')
        if not path_herb or not path_side:raise ValueError('Herb catalog not configured')
        binding=meta['herb_background']
        ranking=similarity.query(path_side,path_herb,None,binding['dataset_version'],tool=call.tool,
            metric=call.arguments.metric,top_k=call.arguments.top_k,
            external_query={'record_id':found['record_id'],'target_ids':found['target_ids'],'background':binding})
        herb_revision=ranking.pop('catalog_revision');ranking['herb_dataset_version']=ranking.pop('dataset_version')
        evidence_con,_=catalog.connect(path)
        with closing(evidence_con):
            evidence_con.execute('BEGIN')
            for item in ranking['items']:
                for pair in item['evidence']:
                    rows=evidence_con.execute('SELECT json_pointer,raw_row FROM target_rows WHERE record_id=? AND uniprot=? ORDER BY ordinal LIMIT 3',(found['record_id'],pair['uniprot'])).fetchall()
                    pair['disease_rows']=[{'json_pointer':r[0],'raw_row':json.loads(r[1])} for r in rows]
        data.update(ranking)
    revision=hashlib.sha256(catalog.dumps(['disease_analysis_v1',before,found['source_fingerprint'],herb_revision]).encode()).hexdigest()
    if before!=similarity.file_token(path) or expected_revision is not None and expected_revision!=revision:
        raise similarity.SnapshotChanged('Disease analysis changed')
    data['catalog_revision']=revision
    return data


def execute_turn(parsed, *, expected_revision=None):
    from tcm.analysis_tools import invoke,command,DiseaseRankingData
    result={'topic':'target_analysis','status':'ok','llm_calls':0,'network_calls':0}
    path=os.getenv('ETCM_DISEASE_DB')
    if not path:
        return {**result,'status':'unavailable','message':'疾病来源索引尚未配置。'},None
    try:found=catalog.lookup(path,parsed.arguments.name,page=getattr(parsed.arguments,'page',1))
    except (OSError,ValueError,sqlite3.Error,KeyError,TypeError):
        return {**result,'status':'unavailable','message':'疾病来源索引暂不可用，请检查数据版本。'},None
    if found['resolution_status']!='source_record':
        options=[{'label':o['name'],'source':o,'query':command(parsed.model_dump(),o['record_id'])} for o in found['options']]
        return {**result,'status':'clarification_needed','message':'请选择疾病来源。' if options else '未找到该疾病来源。请使用数据库原名（例如 Strabismus）或来源ID；当前没有经过核验的中文疾病别名映射。','options':options},None
    current=parsed.model_dump();current['arguments']['name']=found['record_id']
    tool_result=invoke(current,expected_revision=expected_revision)
    if tool_result.status=='error':
        return {**result,'status':'unavailable','message':'来源或分析版本已改变，请重新查询。' if tool_result.error.code=='snapshot_changed' else '疾病分析暂不可用，未生成候选结果。','analysis_result':tool_result.model_dump()},None
    data=tool_result.data
    message=f"{data.profile['name']}：{len(data.target_ids)}个来源靶点（按UniProt去重）。"
    if isinstance(data,DiseaseRankingData):
        message+=f" 按{'Jaccard' if data.metric=='jaccard' else '共同靶点数'}显示{len(data.items)}个药材候选来源。"
    if not data.target_ids:message=f"{data.profile['name']}：当前来源没有可用靶点记录，无法计算候选排序。"
    result.update(message=message,analysis_result=tool_result.model_dump(),replay_query=command(current))
    return result,{'kind':'etcm_analysis_focus_v1','call':current,'catalog_revision':data.catalog_revision}
