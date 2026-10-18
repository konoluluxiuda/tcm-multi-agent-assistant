"""Read-only dataset inventory and fixed capability descriptions, without models."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3

from features import env_bool
from tcm.herbs import SCHEMA_VERSION, _resolve_dataset_version
from tcm.agent_research import runtime as research_runtime

ROOT = Path(__file__).resolve().parents[1]


def herb_inventory(database, version=None):
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        if db.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
            raise ValueError('Unsupported herb schema')
        selected, exists = _resolve_dataset_version(db, version)
        if not exists:
            return {'status': 'dataset_not_found', 'dataset_version': selected}
        total = db.execute("SELECT COUNT(*) FROM records WHERE dataset_version=? AND state='valid'", (selected,)).fetchone()[0]
        rows = db.execute("""SELECT json_extract(document,'$.fields.efficacy_class.value') AS label, COUNT(*)
            FROM records WHERE dataset_version=? AND state='valid'
            AND json_extract(document,'$.fields.efficacy_class.state')='present'
            AND typeof(json_extract(document,'$.fields.efficacy_class.value'))='text'
            AND trim(json_extract(document,'$.fields.efficacy_class.value'))<>''
            GROUP BY label ORDER BY COUNT(*) DESC, label""", (selected,)).fetchall()
        present = sum(n for _, n in rows)
        return {'status': 'ok', 'dataset_version': selected, 'source_record_count': total,
                'efficacy_present_records': present, 'efficacy_missing_records': total-present,
                'raw_efficacy_value_count': len(rows), 'raw_efficacy_values': [{'value': label, 'record_count': n} for label,n in rows[:20]],
                'returned_values': min(len(rows),20), 'truncated': len(rows)>20,
                'count_unit': 'source_record', 'classification_unit': 'distinct_raw_field_value',
                'human_signoff': False}


def execute(request):
    topic = request['topic']
    result = {'status': 'ok', 'topic': topic, 'llm_calls': 0, 'network_calls': 0}
    if topic == 'overview':
        try:
            herbs = herb_inventory(os.getenv('TCM_DB') or ROOT/'var/tcm/herbs.sqlite3', os.getenv('TCM_DATASET_VERSION') or None)
        except (OSError, ValueError, sqlite3.Error, TypeError):
            herbs = {'status': 'unavailable'}
        try:
            service = research_runtime.get_service()
            if service is None:
                research = {'status': 'disabled'}
            else:
                service.check()
                with closing(sqlite3.connect((service.directory/'research.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)) as db:
                    db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
                    entries = db.execute('SELECT COUNT(*) FROM entries').fetchone()[0]
                    chunks = db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
                service.check()
                research = {'status': 'ok', 'entry_count': entries, 'field_chunk_count': chunks,
                            'index_sha256': service.receipt['index_sha256'], 'human_signoff': False}
        except (OSError, ValueError, sqlite3.Error, KeyError, TypeError, RuntimeError):
            research = {'status': 'unavailable'}
        result.update(herbs=herbs, pharmacopoeia=research)
        lines = ['当前本地资料概览（实时读取已导入数据库）：']
        if herbs['status']=='ok':
            lines += [f"ETCM：{herbs['source_record_count']}条有效来源记录。",
                      f"功效分类字段：{herbs['efficacy_present_records']}条有值，{herbs['efficacy_missing_records']}条缺失；非空原始字段值共{herbs['raw_efficacy_value_count']}种。"]
            lines.append('原始分类值及记录数见下方列表（最多20种）；这些来源标签不写入会话历史。')
        else:
            lines.append('ETCM：当前数据不可用或指定版本未导入，未沿用旧数量。')
        if research['status']=='ok':
            lines.append(f"药典研究索引：{research['entry_count']}个候选条目，{research['field_chunk_count']}个字段块，尚未完成人工审核。")
        else:
            lines.append('药典研究索引：未启用或当前不可用，未推测数量。')
        lines += ['记录数不等于去重药材种数；功效原始值未合并、拆分或翻译，不能视为已核验的标准分类。ETCM与药典未完成跨库对象映射，两个数量不能相加。',
                  '可以继续问“黄芪有什么作用”，也可用已收录的拼音或拉丁名查询ETCM属性。']
        result['status']='ok' if herbs['status']=='ok' and research['status']=='ok' else 'partial' if herbs['status']=='ok' or research['status']=='ok' else 'unavailable'
        result['message']='\n'.join(lines)
    elif topic == 'capabilities':
        research='已启用' if env_bool('ENABLE_PHARMACOPOEIA_RESEARCH') else '未启用'
        pubmed='已启用' if env_bool('ENABLE_PUBMED') else '未启用'
        result['message']=f'当前支持：\n• 数据概览：问“收录了多少药材”“有哪些功效分类”，按本地数据库统计。\n• 药典字段与原页证据（{research}）：问“黄芪有什么作用”，再问“用量呢”“出处呢”。\n• ETCM结构化属性：按来源ID或已收录拼音/拉丁/英文名查询；中文跨库映射尚未核验。\n• PubMed文献（{pubmed}）：如“在PubMed检索 Astragalus membranaceus”；只获取摘要及元数据。\n• 证据清单下载、会话保存与清除。\n尚不支持完整术语解释库、自动临床证据评议或患者个体化用药判断。工具已启用不等于资料已就绪；可问“数据概览”检查实际来源。'
    else:
        messages={
            'concept':'已识别为概念解释问题。当前资料是药典药材条目和ETCM字段，尚未建立有来源的术语解释库，不能据此给出可靠的通用定义。你可以指定概念与资料来源；若要查某味药的记载，可直接问“黄芪的归经是什么”。',
            'clarify_sources':'你同时提到了药典与现代文献。请先选查询方向：药典记载、PubMed文献，或先检索文献后在卡片导出药典＋文献清单；当前不会自动假定二者研究对象一致。',
            'literature_term':'已识别为文献检索。请提供英文/拉丁名或明确PubMed检索式，例如“在PubMed检索 Astragalus membranaceus”。中文名到研究对象的映射尚未核验，未自动翻译或联网猜测。',
            'research_name_needed':'当前选择的是ETCM来源记录，尚不能将其自动映射到药典条目。请明确中文药名和字段，例如“黄芪的用量”。',
        }
        result.update(status='clarification_needed',message=messages[topic])
    return result
