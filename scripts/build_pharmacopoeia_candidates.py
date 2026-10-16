"""Build local, unreviewed field cards and source crops from the frozen OCR pilot."""
import argparse
import json
from pathlib import Path
from tcm.pharmacopoeia import build_candidates, digest

ROOT=Path(__file__).resolve().parents[1]


def build(output, ocr_dir, suite_path, flags_path):
    suite=json.loads(suite_path.read_text())
    for source in suite['documents'].values():
        if digest(ROOT/source['path'])!=source['sha256']: raise ValueError('Source PDF changed')
    data=build_candidates(suite,ocr_dir,json.loads(flags_path.read_text()))
    data.update(reference_sha256=digest(suite_path), flags_sha256=digest(flags_path),
                extractor_sha256=digest(ROOT/'tcm/pharmacopoeia.py'))
    import pymupdf
    output.mkdir(parents=True,exist_ok=True)
    assets=output/'assets';assets.mkdir(exist_ok=True)
    crops=0
    for record in data['records']:
        for field,value in record['fields'].items():
            for index,occurrence in enumerate(value['occurrences']):
                groups={}
                for line in occurrence['evidence']:
                    groups.setdefault((line['document'],line['pdf_page']),[]).append(line)
                occurrence['previews']=[]
                for (label,number),lines in groups.items():
                    with pymupdf.open(ROOT/suite['documents'][label]['path']) as pdf:
                        page=pdf[number-1]
                        bounds=[]
                        for line in lines:
                            xs,ys=zip(*line['pdf_box']);box=pymupdf.Rect(min(xs),min(ys),max(xs),max(ys))
                            if not page.rect.contains(box): raise ValueError('OCR box outside PDF page')
                            bounds.append(box)
                            annot=page.add_rect_annot(box);annot.set_colors(stroke=(1,0,0));annot.set_border(width=.6);annot.update()
                        clip=pymupdf.Rect(bounds[0])
                        for box in bounds[1:]:clip|=box
                        clip=(clip+(-10,-10,10,10))&page.rect
                        name=f'{record["id"]}-{field}-{index}-{label}-{number}.png'
                        page.get_pixmap(matrix=pymupdf.Matrix(2,2),clip=clip).save(assets/name)
                        occurrence['previews'].append({'image':'assets/'+name,'document':label,
                            'pdf_page':number,'printed_page':suite['documents'][label]['printed_pages'][str(number)],
                            'source_sha256':suite['documents'][label]['sha256']})
                        crops+=1
    data['source_pdfs_unchanged']=all(digest(ROOT/d['path'])==d['sha256'] for d in suite['documents'].values())
    if not data['source_pdfs_unchanged']:raise RuntimeError('Source PDF changed during preview rendering')
    (output/'candidates.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    (output/'index.html').write_text((ROOT/'tcm/pharmacopoeia_review.html').read_text())
    print(json.dumps({'records':len(data['records']),'quarantined':sum(r['review_status']=='quarantined' for r in data['records']),
                      'source_crops':crops,'source_pdfs_unchanged':True}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,default=ROOT/'var/pharmacopoeia_candidates')
    p.add_argument('--ocr-dir',type=Path,default=ROOT/'var/pharmacopoeia_ocr_sample/final')
    p.add_argument('--suite',type=Path,default=ROOT/'eval/pharmacopoeia_ocr_sample_v1.json')
    p.add_argument('--flags',type=Path,default=ROOT/'eval/pharmacopoeia_ocr_flags_v1.json')
    a=p.parse_args();build(a.out,a.ocr_dir,a.suite,a.flags)
