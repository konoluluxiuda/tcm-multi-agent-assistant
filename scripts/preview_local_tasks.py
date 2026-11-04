"""Run the local ETCM/task demo without external models or online tools."""
import argparse
import hashlib
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = ROOT / 'var/task_literature_v1'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=7877, help='Local HTTP port (default: 7877)')
    parser.add_argument('--state', type=Path, default=DEFAULT_STATE,
                        help='Persistent chat/task/bookmark directory; reuse it after restarting')
    parser.add_argument('--with-intent-model',action='store_true',
                        help='Allow one configured model call with only an unmatched question; no source text/history')
    parser.add_argument('--diseases',type=Path,help='Optional completed disease source catalog for local analysis')
    parser.add_argument('--pharmacopoeia-index', type=Path, default=ROOT/'var/mineru_retrieval_v3',
                        help='Immutable document index; v3 separates post-storage notes; pin an older version only for replay')
    parser.add_argument('--dense-python', type=Path,
                        help='Optional local Python with ONNX/tokenizers/numpy; enables existing local BGE cache, no downloads')
    parser.add_argument('--mcp-python',type=Path,help='Optional isolated MCP SDK runtime; use stdio read-only report tools')
    parser.add_argument('--semantic-memory', action='store_true', help='Enable local project memory retrieval; requires --dense-python, never sends memory to providers')
    parser.add_argument('--qdrant-generation',type=Path,help='Optional immutable local Qdrant generation; requires --dense-python')
    parser.add_argument('--reranker-model',type=Path,help='Optional pinned local ONNX cross-encoder directory; requires --dense-python')
    parser.add_argument('--synthetic-loop',action='store_true',help='Enable authenticated synthetic loop lifecycle; no external models')
    parser.add_argument('--dataops-synthetic', action='store_true',
                        help='Enable D2 observation/HITL/revision with a labelled local model substitute; no network')
    parser.add_argument('--with-langfuse', action='store_true', help='Export DataOps traces to configured Langfuse')
    parser.add_argument('--rag',action='store_true',help='Enable release-bound original/cited-answer page at /rag')
    parser.add_argument('--rag-model',action='store_true',help='Allow one configured RAG generation request per submitted task; requires --rag')
    parser.add_argument('--rag-release',type=Path,default=ROOT/'var/data_releases/r1_20261030')
    parser.add_argument('--research-selection', choices=('fixed','synthetic','native'),
                        help='Enable bounded candidate selection at /dataops/research; native permits up to 10 model requests per task')
    parser.add_argument('--research-goal-model',action='store_true',
                        help='Enable question understanding for chat/workbench; page can opt out; requires --research-selection')
    parser.add_argument('--understanding-policy',choices=('local_then_one_model_v1','unified_v2','requirements_v3'),
                        default='local_then_one_model_v1',help='unified_v2 uses model understanding plus at most one correction or independent task review (2 requests total); requirements_v3 uses the versioned requirement contract')
    parser.add_argument('--langfuse-env', type=Path, default=ROOT/'var/langfuse_local/app.env',
                        help='Private tracing configuration; only tracing keys are loaded')
    args = parser.parse_args()
    if args.rag_model and not args.rag:parser.error('--rag-model requires --rag')
    if args.research_goal_model and not args.research_selection:parser.error('--research-goal-model requires --research-selection')
    if (args.rag or args.research_selection) and not (args.rag_release/'release.json').is_file():parser.error('RAG release is missing')
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    missing_modules = [name for name in ('uvicorn', 'fastapi', 'langgraph', 'langchain_openai',
                                         'pydantic', 'jinja2', 'dotenv', 'multipart', 'openpyxl')
                       if importlib.util.find_spec(name) is None]
    if missing_modules:
        parser.error('当前Python环境缺少依赖：' + ', '.join(missing_modules) +
                     '\n当前解释器：' + sys.executable +
                     '\n请切换到已安装项目依赖的环境，或在项目虚拟环境中运行：'
                     '\npython -m pip install -r requirements-research-chat.txt'
                     '\n环境准备见 docs/PROJECT_DEMO.md 的“启动与复现”。')
    if args.dataops_synthetic and importlib.util.find_spec('langgraph.checkpoint.sqlite') is None:
        parser.error('D2需要 langgraph-checkpoint-sqlite；请安装 requirements-research-chat.txt。')

    state = args.state.resolve()
    files = {
        'TCM_DB': ROOT / 'var/tcm/herbs.sqlite3',
        'ETCM_ENRICHMENT_DB': ROOT / 'var/research_update_20261020/runtime_catalogs/catalogs/enrichment.sqlite3',
        'ETCM_TARGET_DB': ROOT / 'var/research_update_20261020/runtime_catalogs/catalogs/targets-paths.sqlite3',
        'PHARMACOPOEIA_PDF_HIGH': ROOT / 'data/《中国药典》2025年版 一部.pdf',
        'PHARMACOPOEIA_PDF_FULL': ROOT / 'data/《中国药典》2025年版 一部 全本.pdf',
    }
    directories = {
        'PHARMACOPOEIA_INDEX': args.pharmacopoeia_index.resolve(),
        'ETCM_SOURCE_ROOT': ROOT.parent / 'HDCTI/dataset/ETCM2.0',
    }
    if args.rag or args.research_selection:
        # Chat and the workbench must resolve names from the SAME registered
        # release. A custom release must not leave chat on the old sidecar.
        import json
        registered=json.loads((args.rag_release/'sources.private.json').read_text())['config']
        for key,role in (('TCM_DB','herbs'),('ETCM_ENRICHMENT_DB','enrichment'),('ETCM_TARGET_DB','targets'),
                         ('PHARMACOPOEIA_PDF_HIGH','pdf_high'),('PHARMACOPOEIA_PDF_FULL','pdf_full')):
            files[key]=Path(registered[role])
        directories.update(PHARMACOPOEIA_INDEX=args.rag_release.resolve()/'index',
                           ETCM_SOURCE_ROOT=Path(registered['source_root']))
    missing = [str(p) for p in files.values() if not p.is_file()]
    missing += [str(p) for p in directories.values() if not p.is_dir()]
    if missing:
        parser.error('本地资料缺失，请先准备已有索引和数据库：\n' + '\n'.join(missing))

    state_files = {key: state / name for key, name in {
        'CHAT_MEMORY_DB': 'preview-memory.sqlite3',
        'RESEARCH_LOOP_DB': 'research_loop.sqlite3',
        'RESEARCH_TASK_DB': 'preview-tasks.sqlite3',
        'RESEARCH_BOOKMARK_DB': 'preview-bookmarks.sqlite3',
        'PUBMED_DB': 'preview-pubmed.sqlite3',
        'SOURCE_MANIFEST_PATH': 'preview-empty-sources.json',
    }.items()}
    if {p.resolve() for p in state_files.values()} & {p.resolve() for p in files.values()}:
        parser.error('--state must be separate from the read-only source files')
    cookie = ('task_literature_preview_session' if state == DEFAULT_STATE.resolve() else
              'tcm_local_tasks_' + hashlib.sha256(str(state).encode()).hexdigest()[:16])
    environment = {
        'ENABLE_DATAOPS': '1', 'DATAOPS_ROOT': str(state / 'dataops'),
        'ENABLE_RAG_WORKBENCH':'1' if args.rag else '0',
        'ENABLE_RAG_GENERATION':'1' if args.rag_model else '0',
        'RAG_RELEASE_PATH':str(args.rag_release.resolve()),'RAG_RUN_DB':str(state/'rag_answers.sqlite3'),
        'ENABLE_DATAOPS_AGENT': '1' if args.dataops_synthetic else '0',
        'DATAOPS_DRIVER': 'synthetic' if args.dataops_synthetic else 'disabled',
        'ENABLE_DATAOPS_RESEARCH':'1' if args.research_selection else '0',
        'DATAOPS_RESEARCH_DRIVER':args.research_selection or 'disabled',
        'ENABLE_DATAOPS_RESEARCH_GOAL_MODEL':'1' if args.research_goal_model else '0',
        'DATAOPS_UNDERSTANDING_POLICY':args.understanding_policy,
        'ENABLE_SYNTHETIC_LOOP':'1' if args.synthetic_loop else '0',
        'RESEARCH_TOOLS_ONLY': '1', 'ENABLE_NATURAL_ROUTING': '1',
        'ENABLE_RESEARCH_TASKS': '1', 'ENABLE_RESEARCH_PLAN_JOBS':'1', 'ENABLE_RESEARCH_BOOKMARKS': '1',
        'ENABLE_PHARMACOPOEIA_RESEARCH': '1', 'ENABLE_PUBMED': '0',
        'ENABLE_EVIDENCE_ASSISTANT': '0', 'ENABLE_EVIDENCE_AUTO_ROUTING': '0',
        'ENABLE_INTENT_PLANNER':'1' if args.with_intent_model else '0',
        'ENABLE_WEB_SEARCH': '0', 'ENABLE_IMAGE_ANALYSIS': '0', 'ENABLE_SPEECH': '0',
        'LANGCHAIN_TRACING_V2': 'false', 'LANGSMITH_TRACING': 'false',
        'ENABLE_LANGFUSE': '0', 'LANGFUSE_TRACING_ENABLED': 'false',
        'RESEARCH_JOB_DSN':'', 'RESEARCH_CHECKPOINT_DSN':'',
        'CHAT_MODEL': 'local-demo-disabled', 'CHAT_API_KEY': 'local-demo-disabled',
        'CHAT_BASE_URL': 'http://127.0.0.1:9/v1',
        'EMBEDDING_MODEL': 'local-demo-disabled', 'EMBEDDING_API_KEY': 'local-demo-disabled',
        'EMBEDDING_BASE_URL': 'http://127.0.0.1:9/v1', 'EMBEDDING_DIM': '1024',
        'CHAT_COOKIE_NAME': cookie, 'CHAT_COOKIE_SECURE': '0',
        'TCM_DATASET_VERSION': 'local-2026-10-10',
        'ETCM_DISEASE_DB': str(args.diseases.resolve()) if args.diseases else '',
        **{key: str(path) for key, path in {**files, **directories, **state_files}.items()},
    }
    if args.semantic_memory and not args.dense_python:
        parser.error('--semantic-memory requires --dense-python')
    if args.mcp_python and not args.mcp_python.is_file():
        parser.error('MCP Python executable missing')
    environment['RESEARCH_REPORT_TRANSPORT'] = 'mcp' if args.mcp_python else 'direct'
    environment['RESEARCH_MCP_PYTHON'] = str(args.mcp_python.absolute()) if args.mcp_python else ''
    environment['PROJECT_MEMORY_PYTHON'] = str(args.dense_python.absolute()) if args.semantic_memory else ''
    environment['PROJECT_MEMORY_MODEL'] = str(ROOT / 'var/evidence_assistant_v1/local_bge_small_zh') if args.semantic_memory else ''
    if args.reranker_model and not args.dense_python:
        parser.error('--reranker-model requires --dense-python')
    environment['LOCAL_RERANKER_MODEL'] = str(args.reranker_model.resolve()) if args.reranker_model else ''
    if args.qdrant_generation and not args.dense_python:
        parser.error('--qdrant-generation requires --dense-python')
    environment['LOCAL_QDRANT_GENERATION'] = str(args.qdrant_generation.resolve()) if args.qdrant_generation else ''
    if args.dense_python:
        model = ROOT / 'var/evidence_assistant_v1/local_bge_small_zh'
        cache = ROOT / 'var/evidence_assistant_v1/local_dense_cache'
        if not args.dense_python.is_file() or not model.is_dir() or not cache.is_dir():
            parser.error('Local dense Python/model/cache missing; prepare local assets first')
        environment.update(LOCAL_DENSE_PYTHON=str(args.dense_python.absolute()),
                           LOCAL_DENSE_MODEL=str(model), LOCAL_DENSE_CACHE=str(cache))
    else:
        environment.update(LOCAL_DENSE_PYTHON='', LOCAL_DENSE_MODEL='', LOCAL_DENSE_CACHE='')
    if args.diseases:
        from tcm.disease_catalog import connect as disease_connect
        try:
            connection,_=disease_connect(args.diseases);connection.close()
        except Exception:
            parser.error('Disease catalog must be a completed local index')
    if args.with_intent_model:
        from dotenv import dotenv_values
        configured=dotenv_values(ROOT/'.env')
        for key in ('MODEL','API_KEY','BASE_URL'):
            value=(os.getenv('INTENT_'+key) or configured.get('INTENT_'+key) or
                   os.getenv('CHAT_'+key) or configured.get('CHAT_'+key))
            if not value or value=='local-demo-disabled':parser.error('Intent model configuration is missing')
            environment['INTENT_'+key]=value
    if args.rag_model or args.research_selection=='native' or args.research_goal_model:
        from dotenv import dotenv_values
        configured=dotenv_values(ROOT/'.env')
        for key in ('MODEL','API_KEY','BASE_URL'):
            value=os.getenv('RAG_'+key) or configured.get('RAG_'+key) or configured.get('CHAT_'+key)
            if not value or value=='local-demo-disabled':parser.error('RAG model configuration is missing')
            environment['RAG_'+key]=value
    if args.with_langfuse:
        if importlib.util.find_spec('langfuse') is None:
            parser.error('Install optional requirements-observability.txt first')
        if not args.langfuse_env.is_file():
            parser.error('Langfuse configuration missing; run scripts.setup_langfuse_local init')
        from dotenv import dotenv_values
        tracing = dotenv_values(args.langfuse_env)
        for key in ('LANGFUSE_BASE_URL', 'LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY'):
            if not tracing.get(key):
                parser.error('Langfuse configuration incomplete')
            environment[key] = tracing[key]
        environment.update(ENABLE_LANGFUSE='1', LANGFUSE_TRACING_ENABLED='true',
                           TCM_TRACE_CONTENT=tracing.get('TCM_TRACE_CONTENT', '0'))
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest = state_files['SOURCE_MANIFEST_PATH']
    if not manifest.exists():
        with manifest.open('x', encoding='utf-8') as stream:
            stream.write('{"version":1,"sources":{}}\n')
    else:
        import json
        if json.loads(manifest.read_text()) != {'version': 1, 'sources': {}}:
            parser.error('预览的公开来源清单必须为空，请选择其他 --state 目录')
    os.environ.update(environment)
    os.chdir(ROOT)
    from tcm.research_task_local import LocalUnavailable, snapshot
    try:
        snapshot()
    except LocalUnavailable:
        parser.error('ETCM目录不可用或与数据版本不一致，请检查已有运行数据库')

    print(f'任务页：http://127.0.0.1:{args.port}/research-tasks', flush=True)
    print(f'聊天页：http://127.0.0.1:{args.port}/', flush=True)
    print(f'资料工作台：http://127.0.0.1:{args.port}/dataops', flush=True)
    if args.rag:print(f'引用资料页：http://127.0.0.1:{args.port}/rag',flush=True)
    if args.research_selection:print(f'候选核对任务：http://127.0.0.1:{args.port}/dataops/research（{args.research_selection}）',flush=True)
    if args.research_goal_model:print('问句理解：'+('统一模型理解，最多2次请求（首轮加一次纠正或独立复核）' if args.understanding_policy in ('unified_v2','requirements_v3') else '本地优先，最多1次模型兜底')+'；聊天与工作台共用，工作台可取消。任务执行开关独立。',flush=True)
    model_modes=[]
    if args.with_intent_model:model_modes.append('未识别问句可调用意图模型一次')
    if args.rag_model:model_modes.append('引用回答可按需调用模型一次')
    if args.research_selection=='native':model_modes.append('候选任务每任务最多10次模型请求')
    if args.research_goal_model:model_modes.append(f"问句理解每请求编号最多{2 if args.understanding_policy in ('unified_v2','requirements_v3') else 1}次，执行开关独立")
    print(('；'.join(model_modes) if model_modes else '本地资料模式；外部模型关闭。')+' 联网工具关闭。'+
          ('DataOps Langfuse追踪已启用。' if args.with_langfuse else '追踪关闭。')+
          'Ctrl+C停止，同一状态目录可恢复记录。',flush=True)
    import uvicorn
    uvicorn.run('app:app', host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
