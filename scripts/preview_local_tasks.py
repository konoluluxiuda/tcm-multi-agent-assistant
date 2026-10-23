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
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    missing_modules = [name for name in ('uvicorn', 'fastapi', 'langgraph', 'langchain_openai',
                                         'pydantic', 'jinja2', 'dotenv', 'multipart')
                       if importlib.util.find_spec(name) is None]
    if missing_modules:
        parser.error('当前Python环境缺少依赖：' + ', '.join(missing_modules) +
                     '\n当前解释器：' + sys.executable +
                     '\n请切换到已安装项目依赖的环境，或在项目虚拟环境中运行：'
                     '\npython -m pip install -r requirements-research-chat.txt'
                     '\n环境准备见 docs/PROJECT_DEMO.md 的“启动与复现”。')

    state = args.state.resolve()
    files = {
        'TCM_DB': ROOT / 'var/tcm/herbs.sqlite3',
        'ETCM_ENRICHMENT_DB': ROOT / 'var/research_update_20261020/runtime_catalogs/catalogs/enrichment.sqlite3',
        'ETCM_TARGET_DB': ROOT / 'var/research_update_20261020/runtime_catalogs/catalogs/targets-paths.sqlite3',
        'PHARMACOPOEIA_PDF_HIGH': ROOT / 'data/《中国药典》2025年版 一部.pdf',
        'PHARMACOPOEIA_PDF_FULL': ROOT / 'data/《中国药典》2025年版 一部 全本.pdf',
    }
    directories = {
        'PHARMACOPOEIA_INDEX': ROOT / 'var/mineru_retrieval_v2',
        'ETCM_SOURCE_ROOT': ROOT.parent / 'HDCTI/dataset/ETCM2.0',
    }
    missing = [str(p) for p in files.values() if not p.is_file()]
    missing += [str(p) for p in directories.values() if not p.is_dir()]
    if missing:
        parser.error('本地资料缺失，请先准备已有索引和数据库：\n' + '\n'.join(missing))

    state_files = {key: state / name for key, name in {
        'CHAT_MEMORY_DB': 'preview-memory.sqlite3',
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
        'RESEARCH_TOOLS_ONLY': '1', 'ENABLE_NATURAL_ROUTING': '1',
        'ENABLE_RESEARCH_TASKS': '1', 'ENABLE_RESEARCH_BOOKMARKS': '1',
        'ENABLE_PHARMACOPOEIA_RESEARCH': '1', 'ENABLE_PUBMED': '0',
        'ENABLE_EVIDENCE_ASSISTANT': '0', 'ENABLE_EVIDENCE_AUTO_ROUTING': '0',
        'ENABLE_INTENT_PLANNER':'1' if args.with_intent_model else '0',
        'ENABLE_WEB_SEARCH': '0', 'ENABLE_IMAGE_ANALYSIS': '0', 'ENABLE_SPEECH': '0',
        'LANGCHAIN_TRACING_V2': 'false', 'LANGSMITH_TRACING': 'false',
        'CHAT_MODEL': 'local-demo-disabled', 'CHAT_API_KEY': 'local-demo-disabled',
        'CHAT_BASE_URL': 'http://127.0.0.1:9/v1',
        'EMBEDDING_MODEL': 'local-demo-disabled', 'EMBEDDING_API_KEY': 'local-demo-disabled',
        'EMBEDDING_BASE_URL': 'http://127.0.0.1:9/v1', 'EMBEDDING_DIM': '1024',
        'CHAT_COOKIE_NAME': cookie, 'CHAT_COOKIE_SECURE': '0',
        'TCM_DATASET_VERSION': 'local-2026-10-10',
        'ETCM_DISEASE_DB': str(args.diseases.resolve()) if args.diseases else '',
        **{key: str(path) for key, path in {**files, **directories, **state_files}.items()},
    }
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
    print(('仅未识别问句可调用意图模型一次；本地资料和会话历史不外发。' if args.with_intent_model else
           '本地资料模式；外部模型关闭。')+' 联网工具和追踪关闭。Ctrl+C停止，同一状态目录可恢复记录。',flush=True)
    import uvicorn
    uvicorn.run('app:app', host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
