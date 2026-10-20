"""Run the opt-in model workflow; full output stays in a private local artifact."""
import argparse
import json
import os
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('question')
    parser.add_argument('--out',type=Path,required=True)
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--synthetic',action='store_true',help='Use synthetic fixtures (default)')
    group.add_argument('--local-sources',action='store_true',help='Send configured source excerpts to the model; use only with data-transfer authorization')
    args=parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv()
    os.environ['LANGCHAIN_TRACING_V2']='false';os.environ['LANGSMITH_TRACING']='false'
    from tcm.evidence_assistant import EvidenceAssistant
    args.out.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    result=EvidenceAssistant(synthetic=not args.local_sources).run(args.question)
    fd=os.open(args.out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:result[k] for k in ('status','model_calls','duration_ms','input_tokens','output_tokens','cost')},ensure_ascii=False))


if __name__=='__main__':main()
