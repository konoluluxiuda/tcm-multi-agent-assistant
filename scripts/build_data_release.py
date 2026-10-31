"""Build or verify an immutable, source-grounded local R1 research release."""
import argparse
import json
from pathlib import Path

from tcm.data_release import build, check

ROOT = Path(__file__).resolve().parents[1]


def defaults():
    return {'base':ROOT/'var/mineru_pharmacopoeia_v5','revisions':ROOT/'var/mineru_revisions_v3',
            'index':ROOT/'var/mineru_retrieval_v3','herbs':ROOT/'var/tcm/herbs.sqlite3',
            'enrichment':ROOT/'var/research_update_20261020/runtime_catalogs/catalogs/enrichment.sqlite3',
            'targets':ROOT/'var/research_update_20261020/runtime_catalogs/catalogs/targets-paths.sqlite3',
            'source_root':ROOT.parent/'HDCTI/dataset/ETCM2.0',
            'pdf_high':ROOT/'data/《中国药典》2025年版 一部.pdf',
            'pdf_full':ROOT/'data/《中国药典》2025年版 一部 全本.pdf',
            'dense_cache':ROOT/'var/evidence_assistant_v1/local_dense_cache',
            'seeds':ROOT/'eval/rag_v1/seeds.json','references':ROOT/'eval/rag_v1/references.json'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True,help='Fresh release directory, or existing one for --check')
    parser.add_argument('--config',type=Path,help='Explicit JSON source paths, no dotenv or model configuration')
    parser.add_argument('--register-index',action='store_true',help='Register existing lexical index instead of rebuilding it')
    parser.add_argument('--check',action='store_true',help='Read-only integrity check; never run retrieval or models')
    args=parser.parse_args()
    if args.check:
        report=check(args.out)
    else:
        config=json.loads(args.config.read_text()) if args.config else defaults()
        r=build(config,args.out,rebuild_index=not args.register_index)
        report={k:r[k] for k in ('status','release_id','content_id','herbs','pharmacopoeia','benchmark','legacy_vectors')}
        report['quarantined_name_candidates']=len(r['sidecars']['mapping']['quarantined_candidates'])
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
