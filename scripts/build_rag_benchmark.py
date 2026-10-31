"""Separate candidate generation, reviewed-reference freeze and raw-source checks."""
import argparse
import json
from pathlib import Path

from scripts.build_data_release import defaults
from tcm.rag_benchmark import RawCorpus, candidates, check_references, freeze, read, write


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--candidates',action='store_true',help='Generate unreviewed templates only, never gold')
    mode.add_argument('--freeze-seeds',type=Path,help='Freeze explicitly reviewed raw-source assignments')
    mode.add_argument('--check',action='store_true',help='Verify --out reference file, no retrieval or model')
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--config',type=Path,help='Explicit local source paths; no dotenv or model configuration')
    args=parser.parse_args()
    if not args.check and args.out.exists():parser.error('Preserve previous candidates/references; use a new output')
    c=read(args.config) if args.config else defaults()
    corpus=RawCorpus(c['base'],c['source_root'],{'high':c['pdf_high'],'full':c['pdf_full']})
    if args.check:
        report=check_references(read(args.out),corpus)
    elif args.candidates:
        rows=candidates(corpus);args.out.parent.mkdir(parents=True,exist_ok=True)
        write(args.out,{'kind':'unreviewed_candidates_not_gold','cases':rows})
        report={'status':'candidates_generated','candidates':len(rows),'frozen_references':0}
    else:
        reference=freeze(read(args.freeze_seeds),corpus);args.out.parent.mkdir(parents=True,exist_ok=True)
        write(args.out,reference);report=check_references(reference,corpus)
    print(json.dumps({**report,'model_calls':0,'retrieval_runs':0},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
