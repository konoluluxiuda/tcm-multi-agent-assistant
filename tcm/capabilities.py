"""Deployment capability descriptions, without models or source ingestion.

Configuration/file checks describe available entry points, not successful execution.
The task APIs remain responsible for validating schemas and source revisions.
"""
import os
from pathlib import Path

from features import env_bool

ROOT = Path(__file__).resolve().parents[1]


def dataops_runtime():
    enabled = env_bool('ENABLE_DATAOPS')
    driver = os.getenv('DATAOPS_DRIVER', 'disabled')
    return {'agent': enabled and env_bool('ENABLE_DATAOPS_AGENT') and driver == 'synthetic'
                    and not bool(os.getenv('RESEARCH_JOB_DSN')),
            'driver': driver, 'external_model_enabled': research_runtime()['external_model_enabled'],
            'question_compiler': 'local_bounded_grammar_v1',
            'research_selection': research_runtime()}


def research_runtime():
    driver = os.getenv('DATAOPS_RESEARCH_DRIVER', 'disabled')
    ready = (env_bool('ENABLE_DATAOPS') and env_bool('ENABLE_DATAOPS_RESEARCH') and
             driver in ('fixed', 'synthetic', 'native') and not os.getenv('RESEARCH_JOB_DSN'))
    question_model=bool(ready and env_bool('ENABLE_DATAOPS_RESEARCH_GOAL_MODEL') and
                all(os.getenv(k) for k in ('RAG_MODEL','RAG_BASE_URL','RAG_API_KEY')))
    execution_model=bool(ready and driver=='native')
    return {'enabled': bool(ready), 'driver': driver,
            'external_model_enabled':execution_model or question_model,
            'execution_model_enabled':execution_model,
            'question_model_enabled':question_model,
            'goal_input': 'bounded_question_or_form', 'identity_approval': False}


def describe():
    def file(name, default=''):
        value = os.getenv(name) or default
        return bool(value) and Path(value).is_file()

    base = file('TCM_DB', str(ROOT / 'var/tcm/herbs.sqlite3'))
    enrichment = base and file('ETCM_ENRICHMENT_DB')
    disease = file('ETCM_DISEASE_DB')
    index = Path(os.getenv('PHARMACOPOEIA_INDEX') or ROOT / 'var/mineru_retrieval_v3')
    pharma = env_bool('ENABLE_PHARMACOPOEIA_RESEARCH')
    index_present = (index / 'research.sqlite3').is_file() and (index / 'manifest.json').is_file()
    documents = index_present and file('PHARMACOPOEIA_PDF_HIGH', str(ROOT / 'data/《中国药典》2025年版 一部.pdf')) and file('PHARMACOPOEIA_PDF_FULL', str(ROOT / 'data/《中国药典》2025年版 一部 全本.pdf'))
    # The workbench reads index fields directly, and requires explicit source paths.
    workspace_sources = (base and enrichment and index_present and
                         all(os.getenv(key) for key in ('TCM_DB', 'ETCM_ENRICHMENT_DB', 'PHARMACOPOEIA_INDEX')))
    runtime = dataops_runtime()
    entries = []

    def add(identifier, label, enabled, configured, example):
        entries.append(dict(id=identifier, label=label, enabled=enabled,
                            configured=configured, example=example))

    add('overview', '数据概览、数据库介绍', True, True, '介绍两个数据库')
    add('herbs', 'ETCM药材属性', True, base, '查看白术的属性')
    add('relations', '药材成分、靶点及原始来源', True, enrichment, '白术有哪些成分')
    add('pharmacopoeia', '药典功效、注意事项、字段对比和出处', pharma, documents, '黄芪的功效和注意事项')
    add('targets', '共同靶点、靶点画像和药材相似度', True, enrichment, '当归与川芎的共同靶点')
    add('diseases', '疾病目录和疾病靶点', True, disease, '提供两个疾病及ID')
    add('disease_ranking', '按疾病靶点与药材关联排序', True, disease and enrichment, '按疾病靶点查询关联药材')
    add('bookmarks', '收藏、证据笔记导出', env_bool('ENABLE_RESEARCH_BOOKMARKS', env_bool('RESEARCH_TOOLS_ONLY')), True, '在结果卡片中收藏或导出')
    add('workbench', '资料工作台手动核对与表格导出', env_bool('ENABLE_DATAOPS'), bool(workspace_sources), '打开资料核对工作台')
    add('dataops_agent', '批量整理、补充字段和Excel交付（本地模拟模型）', runtime['agent'], bool(workspace_sources), '整理白术和黄芪的功效、注意事项，保留出处并生成表格')
    selection = runtime['research_selection']
    selection_mode=('真实模型执行，每任务最多10次请求' if selection['execution_model_enabled'] else
                    '本地确定性执行' if selection['driver']=='fixed' else '本地模拟策略')
    if selection['question_model_enabled']:selection_mode+='；可选单次模型理解问句'
    add('research_selection', '资料问句、候选核对与增量交付（'+selection_mode+'）',
        selection['enabled'], (Path(os.getenv('RAG_RELEASE_PATH','var/data_releases/r1_20261030'))/'release.json').is_file(),
        '打开 /dataops/research，输入整理要求或选择候选和字段')
    available = [x for x in entries if x['enabled'] and x['configured']]
    unavailable = [x for x in entries if not x['enabled'] or not x['configured']]
    message = '当前可用入口：\n' + '\n'.join('• ' + x['label'] for x in available)
    if runtime['agent'] and any(x['id'] == 'dataops_agent' for x in available):
        message += '\n可试：整理白术和黄芪的功效、注意事项，保留出处并生成表格。'
    notes = ['按部署开关和文件配置展示；未在本次帮助请求中执行任务或验证数据完整性。',
             '真实LLM自主整理尚未验收通过；当前不提供个体选药、处方或疾病疗效预测。']
    if unavailable:
        notes.append('未启用或资料未配置：' + '；'.join(x['label'] for x in unavailable) + '。')
    return dict(message=message, notes=notes, capabilities=entries, dataops=runtime,
                readiness_checked=False)
