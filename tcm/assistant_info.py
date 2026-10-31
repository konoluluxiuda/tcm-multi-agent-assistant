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
    if topic=='target_analysis':
        from tcm.analysis_tools import execute_turn
        return execute_turn(request)[0]
    if topic == 'dataset_info':
        from tcm.dataset_info import describe
        return describe(execute({'topic':'overview'}), request.get('datasets'))
    if topic == 'etcm_relations':
        from tcm.relation_queries import execute as execute_relations
        return execute_relations(request)
    result = {'status': 'ok', 'topic': topic, 'llm_calls': 0, 'network_calls': 0}
    if topic=='intent_clarification':
        message=('当前只提供资料查询，不能执行价格交易或个体选药、处方任务。' if request.get('unsupported') else
                 '请说明要了解的资料或查询内容，例如介绍数据库、药材属性、成分或共同靶点。')
        if request.get('planner_failed'):message='暂时未能识别这句话，请换一种说法，例如“介绍两个数据库”或“查看白术的属性”。'
        return {**result,'status':'clarification_needed','message':message}
    if topic == 'research_task_review':
        question=request['question']
        if not env_bool('ENABLE_RESEARCH_TASKS'):
            return {**result,'status':'clarification_needed','can_create_task':False,
                    'message':'这条复合查询需要先核对请求清单，但当前部署尚未启用研究任务。'
                              '原句没有被拆成较小查询或执行；请使用已启用研究任务的入口。'}
        if not isinstance(question,str) or not 1<=len(question)<=400:
            return {**result,'status':'clarification_needed','can_create_task':False,
                    'message':'研究任务每题最多400字符。请缩短原句或明确拆分任务后重试；未截断原请求或执行查询。'}
        return {**result,'status':'clarification_needed','can_create_task':True,
                'task_proposal':{'mode':'etcm_question','question':question},
                'message':'这个问题包含多个要求，请先确认本次要查询的范围。',
                'notes':['完整原句保存在本地研究任务中，尚未执行查询。',
                         '当前一次执行两味药的共同标识及双方记录，其他要求保留为未完成。']}
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
        lines=[]
        notes=['记录数不是去重药材种数；ETCM与药典未完成对象对应，两个数量不能相加。']
        if herbs['status']=='ok':
            lines.append(f"ETCM：{herbs['source_record_count']}条药材来源记录。")
            notes.append(f"功效分类：{herbs['efficacy_present_records']}条有值、{herbs['efficacy_missing_records']}条缺失；原始值共{herbs['raw_efficacy_value_count']}种，未统一分类。")
        else:
            lines.append('ETCM：当前数据不可用或指定版本未导入，未沿用旧数量。')
        if research['status']=='ok':
            lines.append(f"药典：{research['entry_count']}个条目，{research['field_chunk_count']}个字段。")
            notes.append('药典提取尚未完成人工审核。')
        else:
            lines.append('药典研究索引：未启用或当前不可用，未推测数量。')
        result['status']='ok' if herbs['status']=='ok' and research['status']=='ok' else 'partial' if herbs['status']=='ok' or research['status']=='ok' else 'unavailable'
        result['message']='\n'.join(lines)
        result['notes']=notes
    elif topic == 'capabilities':
        from tcm.capabilities import describe
        result.update(describe())

    else:
        guidance = {
            'personal_medication_boundary': '当前服务不能根据你的症状选药、配药或提供个体处方。可以查询明确药材的药典记载和原页来源；这些资料不能代替针对个人情况的专业判断。',
            'source_selection_help': '同名候选不等于同一药材对象，当前没有足够信息替你自动选择。请在候选卡核对拼音、药用部位、来源ID及原始出处，再点击你要研究的来源；不知道差异时请提供具体候选ID，不能仅凭中文名自动合并。',
            'association_evidence_scope': '页面的“来源收录成分”表示本地数据库来源行列出了这条成分记录。当前没有核验它是否来自该药材的实际检测，不能把收录或关联直接当作实验检出、结合或机制证据。请选定具体药材来源并展开原始行、JSON位置及抓取状态，再核对实验依据；缺少依据时保留未知。',
            'relation_source_needed': '当前没有选定你指的具体成分—靶点来源行，不能仅凭药材名或“这个成分”猜测记录。请先查询明确药材的成分靶点候选路径并选择来源，或按明确靶点查询成分，再展开对应条目的原始行与出处；候选路径保留双方证据，不等同已验证机制。',
        }
        if topic in guidance:
            return {**result, 'status': 'clarification_needed', 'message': guidance[topic]}
        if topic == 'target_direction_needed':
            token = request['target']
            return {**result, 'status': 'clarification_needed',
                    'choices': [{'label': '查询关联药材', 'query': token+'关联哪些药材'},
                                {'label': '查询靶点成分', 'query': token+'有哪些成分'}], 'message':
                    f'已识别你提供的靶点标识“{token}”。药材来源与靶点成分是两个查询方向，请选择本次要查哪一种：'
                    f'“{token}关联哪些药材”或“{token}有哪些成分”。仅按当前已核对目录读取；标识未覆盖会明确提示，不代表没有关联。未自动执行其中一半任务。'}
        messages={
            'concept':'已识别为概念解释问题。当前资料是药典药材条目和ETCM字段，尚未建立有来源的术语解释库，不能据此给出可靠的通用定义。你可以指定概念与资料来源；若要查某味药的记载，可直接问“黄芪的归经是什么”。',
            'clarify_sources':'你同时提到了药典与现代文献。请先选查询方向：药典记载、PubMed文献，或先检索文献后在卡片导出药典＋文献清单；当前不会自动假定二者研究对象一致。',
            'literature_term':'已识别为文献检索。请提供英文/拉丁名或明确PubMed检索式，例如“在PubMed检索 Astragalus membranaceus”。中文名到研究对象的映射尚未核验，未自动翻译或联网猜测。',
            'research_name_needed':'当前选择的是ETCM来源记录，尚不能将其自动映射到药典条目。请明确中文药名和字段，例如“黄芪的用量”。',
        }
        result.update(status='clarification_needed',message=messages[topic])
        if topic in ('literature_term', 'clarify_sources'):
            result['message'] = '文献检索已退役。可查询本地药典/ETCM资料，或使用资料核对工作台；原始资料的参考出处继续保留。'
        if topic == 'literature_term' and request.get('wants_export'):
            result['message'] += (' 你还要求下载笔记：当前不会自动把这段不明确的对话整理成报告，'
                                  '需要先完成明确来源的查询，再用卡片的证据Markdown或收藏的“导出证据笔记”；'
                                  '未检索到的论文不会写成已获取证据。')
    return result
