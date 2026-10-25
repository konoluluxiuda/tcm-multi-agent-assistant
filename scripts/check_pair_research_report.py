"""Local paired reports: real data stays in network-disabled child processes."""
import argparse
import asyncio
import json
import os
from pathlib import Path
from tcm.pair_research_report import run_pair_report


async def check(out):
    cases=[]
    for names,question in [(['当归','川芎'],'功能与主治'),(['白术','黄芪'],'功能与主治'),(['当归','不存在的合成药'],'功能与主治')]:
        result=await run_pair_report({'herbs':names,'question':question})
        target=result['targets']
        row={'herbs':names,'status':result['status'],'comparison_rows':len(result['rows']),
             'target_status':target['status'],'target_total':target.get('total'),'target_rows':len(target.get('items',[])),
             'branch_statuses':[r['status'] for r in result['source_reports']],
             'two_sided_witnesses':all(len(i['sources'])==2 and all(s['witnesses'] for s in i['sources']) for i in target.get('items',[]))}
        cases.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
    passed=all(c['target_status']=='ok' and c['comparison_rows'] and c['two_sided_witnesses'] for c in cases[:2]) and cases[2]['status']=='partial' and cases[2]['target_status']=='unresolved_sources'
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps({'status':'passed' if passed else 'failed','external_model_calls':0,'cases':cases},ensure_ascii=False,indent=2))
    if not passed:raise SystemExit(1)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]
    os.environ.update(TCM_DB=str(root/'var/tcm/herbs.sqlite3'),
        ETCM_ENRICHMENT_DB=str(root/'var/research_update_20261020/runtime_catalogs/catalogs/enrichment.sqlite3'),
        TCM_DATASET_VERSION='local-2026-10-10',PHARMACOPOEIA_INDEX=str(root/'var/mineru_retrieval_v2'),
        LANGSMITH_TRACING='false',LANGCHAIN_TRACING_V2='false',
        LOCAL_DENSE_PYTHON='',LOCAL_DENSE_MODEL='',LOCAL_DENSE_CACHE='')
    asyncio.run(check(args.out))


if __name__=='__main__':main()
