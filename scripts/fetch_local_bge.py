"""Download only pinned public BGE files. Run separately from offline source encoding."""
import argparse
import json
import os
from pathlib import Path
import urllib.request
from tcm.mineru_pharmacopoeia import digest

REPO='Xenova/bge-small-zh-v1.5'
REVISION='75c43b069aac4d136ba6bc1122f995fedcfd2781'
FILES={
    'config.json':'d4193ead3a810fd694fa8a31d7fc72fbaebc0668b603e398734bf2f6538ff42f',
    'tokenizer.json':'48cea5d44424912a6fd1ea647bf4fe50b55ab8b1e5879c3275f80e339e8fae26',
    'tokenizer_config.json':'e6f3b96db926a37d4039995fbf5ad17de158dfb8f6343d607e4dbaad18d75f5a',
    'onnx/model.onnx':'69a0b846f4f116b5e6aabf9546ea6754d02264f3211a13a1bd69b31b8040749a',
}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=Path('var/evidence_assistant_v1/local_bge_small_zh'))
    args=parser.parse_args();args.out.mkdir(parents=True,mode=0o700,exist_ok=True)
    rows=[];downloads=0
    for name,expected in FILES.items():
        path=args.out/name;path.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
        if not path.exists():
            temporary=path.with_name(path.name+'.part')
            with urllib.request.urlopen(f'https://huggingface.co/{REPO}/resolve/{REVISION}/{name}',timeout=60) as response,temporary.open('xb') as output:
                while chunk:=response.read(1024*1024):output.write(chunk)
            if digest(temporary)!=expected:raise ValueError('Downloaded model hash mismatch')
            os.chmod(temporary,0o600);temporary.rename(path);downloads+=1
        if digest(path)!=expected:raise ValueError('Existing model file differs; do not silently overwrite')
        rows.append({'file':name,'bytes':path.stat().st_size,'sha256':expected})
    receipt={'repo':REPO,'revision':REVISION,'base_model':'BAAI/bge-small-zh-v1.5',
             'base_revision':'7999e1d3359715c523056ef9478215996d62a620','files':rows}
    manifest=args.out/'download.json'
    if manifest.exists():
        if json.loads(manifest.read_text())!=receipt:raise ValueError('Existing receipt differs')
    else:
        fd=os.open(manifest,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as output:json.dump(receipt,output,indent=2)
    print(json.dumps({'verified_files':len(rows),'downloaded_files':downloads,'repository':REPO,'revision':REVISION}))


if __name__=='__main__':main()
