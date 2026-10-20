"""Prepare a pinned public Argos EN→ZH model, separately from private data processing."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request
import zipfile

URL='https://argos-net.com/v1/translate-en_zh-1_9.argosmodel'
SHA256='433e7c4f034d87fbe2353161e05f18646d7999452f801a4e1f0378522b9850ab'
FILES=('metadata.json','sentencepiece.model','model/config.json','model/model.bin','model/shared_vocabulary.json','README.md')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package',type=Path,default=Path('var/etcm_enrichment_v1/model_download/translate-en_zh-1_9.argosmodel'))
    parser.add_argument('--out',type=Path,default=Path('var/etcm_enrichment_v1/translation_model'))
    parser.add_argument('--download',action='store_true',help='Download the public package if absent; no private inputs are read')
    args=parser.parse_args()
    if args.out.exists():raise FileExistsError('Use a fresh model output directory')
    if not args.package.exists():
        if not args.download:parser.error('Model package absent; pass --download to fetch the public model')
        args.package.parent.mkdir(parents=True,exist_ok=True)
        request=urllib.request.Request(URL,headers={'User-Agent':'Mozilla/5.0'})
        with urllib.request.urlopen(request,timeout=60) as response,args.package.open('xb') as output:
            shutil.copyfileobj(response,output)
    if hashlib.sha256(args.package.read_bytes()).hexdigest()!=SHA256:raise ValueError('Model package SHA-256 mismatch')
    args.out.mkdir(parents=True,mode=0o700)
    hashes={}
    with zipfile.ZipFile(args.package) as package:
        for name in FILES:
            entry=package.getinfo('translate-en_zh-1_9/'+name)
            if entry.file_size>100*1024*1024:raise ValueError('Unexpected model file size')
            target=args.out/name;target.parent.mkdir(parents=True,exist_ok=True)
            with package.open(entry) as source,target.open('xb') as output:shutil.copyfileobj(source,output)
            hashes[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    (args.out/'receipt.json').write_text(json.dumps({'provider':'Argos Translate','version':'en_zh-1.9',
        'url':URL,'package_sha256':SHA256,'files':hashes},indent=2)+'\n')
    print(json.dumps({'status':'prepared','files':len(hashes),'model':str(args.out)}))


if __name__=='__main__':main()
