"""Local-only real-source acceptance. Saves metrics/statuses, not full source excerpts."""
import argparse
import asyncio
import json
import os
from pathlib import Path
from tcm.cross_source_report import run_report


async def check(out):
    rows=[]
    for herb,question in [('白术','功效及注意事项'),('当归','性味与归经'),('黄芪','功能与主治'),('不存在的合成药','功效'),('白术','川芎的功效')]:
        report=await run_report({'herb':herb,'question':question})
        row={'herb':herb,'question':question,'status':report['status'],
             'text_status':report['text']['status'],'structured_status':report['structured']['status'],
             'chunks':[h['chunk_id'] for h in report['text'].get('hits',[])],
             'fields':len(report['structured'].get('rows',[])),
             'retrieval':report['text'].get('retrieval'),
             'branch_ms':{k:report[k]['duration_ms'] for k in ('text','structured')}}
        rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
    passed=all(r['chunks'] and r['fields'] for r in rows[:3]) and not rows[3]['chunks'] and not rows[3]['fields'] and rows[4]['text_status']=='multiple_entities'
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps({'status':'passed' if passed else 'failed','external_model_calls':0,'cases':rows},ensure_ascii=False,indent=2))
    if not passed:raise SystemExit(1)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True);parser.add_argument('--dense-python',type=Path)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    os.environ.update(TCM_DB=str(root/'var/tcm/herbs.sqlite3'),
        ETCM_ENRICHMENT_DB=str(root/'var/research_update_20261020/runtime_catalogs/catalogs/enrichment.sqlite3'),
        TCM_DATASET_VERSION='local-2026-10-10',PHARMACOPOEIA_INDEX=str(root/'var/mineru_retrieval_v2'),
        LANGSMITH_TRACING='false',LANGCHAIN_TRACING_V2='false',
        LOCAL_DENSE_PYTHON=str(args.dense_python.absolute()) if args.dense_python else '',
        LOCAL_DENSE_MODEL=str(root/'var/evidence_assistant_v1/local_bge_small_zh') if args.dense_python else '',
        LOCAL_DENSE_CACHE=str(root/'var/evidence_assistant_v1/local_dense_cache') if args.dense_python else '')
    asyncio.run(check(args.out))


if __name__=='__main__':main()
