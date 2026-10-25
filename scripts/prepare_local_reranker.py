"""Download only pinned public ONNX reranker assets; never reads user corpora."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

REPO='Xenova/bge-reranker-base'
REVISION='cbd4c47f870c4bb77534e06b8853de9c6e958b82'
FILES={
 'config.json':(782,'git','ef36f7221740ddc57b6cfae14977840d1fc0fc95'),
 'tokenizer_config.json':(443,'git','059214673d9d6d2ee319411e2ffec8c024b816d5'),
 'tokenizer.json':(17098079,'sha256','48564c5c7d3fa64d85d95e65414a542385f88b0f128fd8d4163fd7a57f2be05c'),
 'onnx/model_quantized.onnx':(279301077,'sha256','dd98f3e67837d23210a6b7550c08cced4f61845b940ac45be3565840a10f3244'),
}


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
 p.add_argument('--download',action='store_true');args=p.parse_args()
 rows=[]
 for name,(size,kind,expected) in FILES.items():
  target=args.out/name
  if not target.is_file():
   if not args.download:raise FileNotFoundError('Use --download to prepare public model assets')
   target.parent.mkdir(parents=True,exist_ok=True)
   temporary=target.with_suffix(target.suffix+'.part')
   try:
    subprocess.run(['curl','--fail','--location','--retry','2','--connect-timeout','20',
                    '--max-time','600','--max-filesize',str(size),'--silent','--show-error',
                    f'https://huggingface.co/{REPO}/resolve/{REVISION}/{name}?download=true',
                    '--output',str(temporary)],check=True,timeout=1900)
    if temporary.stat().st_size!=size:raise ValueError('Incomplete download')
    data=temporary.read_bytes()
    checksum=hashlib.sha256(data).hexdigest() if kind=='sha256' else hashlib.sha1(f'blob {len(data)}\0'.encode()+data).hexdigest()
    if checksum!=expected:raise ValueError('Public asset checksum mismatch')
    temporary.replace(target)
   finally:temporary.unlink(missing_ok=True)
  data=target.read_bytes()
  checksum=hashlib.sha256(data).hexdigest() if kind=='sha256' else hashlib.sha1(f'blob {len(data)}\0'.encode()+data).hexdigest()
  if len(data)!=size or checksum!=expected:raise ValueError('Existing asset mismatch')
  rows.append({'file':name,'bytes':size,'sha256':hashlib.sha256(data).hexdigest()})
  print(json.dumps({'verified':name,'bytes':size}),flush=True)
 (args.out/'download.json').write_text(json.dumps({'repo':REPO,'revision':REVISION,'files':rows},indent=2))


if __name__=='__main__':main()
