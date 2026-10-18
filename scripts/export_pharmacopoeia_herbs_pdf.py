"""Extract the herb chapter losslessly, preserve provenance, and make bounded upload parts."""
import argparse
from contextlib import ExitStack
import csv
import hashlib
import json
from pathlib import Path

from tcm.pharmacopoeia import digest

ROOT=Path(__file__).resolve().parents[1]


def write_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def page_signature(document,number,image_cache):
    """Compare content operators, image payloads/placements and page geometry, without OCR."""
    page=document[number]
    def image_hash(xref):
        key=(document.name,xref)
        if key not in image_cache:
            image_cache[key]=hashlib.sha256(document.extract_image(xref)['image']).hexdigest()
        return image_cache[key]
    images=[]
    for item in page.get_images(full=True):
        xref,mask=item[:2]
        images.append({'sha256':image_hash(xref),'mask_sha256':image_hash(mask) if mask else None,
                       'rectangles':sorted([list(r) for r in page.get_image_rects(xref)])})
    images.sort(key=lambda x:json.dumps(x,sort_keys=True))
    return {'mediabox':list(page.mediabox),'cropbox':list(page.cropbox),'rotation':page.rotation,
            'content_sha256':[hashlib.sha256(document.xref_stream(x)).hexdigest() for x in page.get_contents()],
            'images':images}


def build_map(plan,ranges):
    result=[]
    for block in ranges:
        label=block['document']
        for number in range(block['first_pdf_page'],block['last_pdf_page']+1):
            known=plan['observed_printed_pages'].get(label,{}).get(str(number))
            result.append({'chapter_pdf_page':len(result)+1,'source_document':label,
                           'source_pdf_page':number,'source_sha256':plan['documents'][label]['sha256'],
                           'expected_printed_page':len(result)+plan['expected_first_printed_page'],
                           'observed_printed_page':known,
                           'printed_label_status':'anchor_visually_observed_pending_human_review' if known is not None
                                                  else 'expected_sequence_not_individually_verified'})
    if len(result)!=plan['expected_pages']:
        raise ValueError('Chapter page count does not match frozen plan')
    return result


def export(plan_path,destination):
    import pymupdf
    plan=json.loads(plan_path.read_text())
    if destination.exists():
        raise FileExistsError('Use a fresh destination; never overwrite previous PDF artifacts')
    for source in plan['documents'].values():
        if digest(ROOT/source['path'])!=source['sha256']:
            raise ValueError('Source PDF changed')
    maps={key:build_map(plan,plan[key+'_ranges']) for key in ('preferred','reference')}
    destination.mkdir(parents=True,exist_ok=False)
    image_cache={}
    products=[]
    with ExitStack() as stack:
        sources={label:stack.enter_context(pymupdf.open(ROOT/spec['path'])) for label,spec in plan['documents'].items()}
        signatures={}
        for key,title in [('preferred','药材和饮片_高清补页版'),('reference','药材和饮片_全本原扫描版')]:
            target=destination/(title+'.pdf')
            with pymupdf.open() as doc:
                for block in plan[key+'_ranges']:
                    doc.insert_pdf(sources[block['document']],from_page=block['first_pdf_page']-1,
                                   to_page=block['last_pdf_page']-1)
                doc.set_metadata({'title':title,'subject':'Chapter-only local derivative; see export_report.json and page_map.json',
                                  'producer':'TCM local chapter export; original scan images preserved'})
                doc.save(target,garbage=4,deflate=True)
            with pymupdf.open(target) as doc:
                if doc.page_count!=plan['expected_pages']:
                    raise ValueError('Exported page count mismatch')
                for number,row in enumerate(maps[key]):
                    signature_key=(row['source_document'],row['source_pdf_page'])
                    if signature_key not in signatures:
                        signatures[signature_key]=page_signature(sources[row['source_document']],row['source_pdf_page']-1,image_cache)
                    if page_signature(doc,number,image_cache)!=signatures[signature_key]:
                        raise ValueError(f'Page content or scan changed during export: {key} page {number+1}')
            products.append({'role':key,'file':target.name,'pages':len(maps[key]),'bytes':target.stat().st_size,
                             'sha256':digest(target),'verified_against_source_pages':len(maps[key])})
            print(json.dumps({'exported':target.name,'pages':len(maps[key]),'verified':True}),flush=True)
        part_dir=destination/'上传分卷';part_dir.mkdir()
        with pymupdf.open(destination/products[0]['file']) as master:
            first=0
            while first<master.page_count:
                count=min(plan['part_pages'],master.page_count-first)
                while True:
                    last=first+count-1
                    target=part_dir/f'药材和饮片_第{first+1:03}-{last+1:03}页.pdf'
                    with pymupdf.open() as part:
                        part.insert_pdf(master,from_page=first,to_page=last)
                        part.save(target,garbage=4,deflate=True)
                    if target.stat().st_size<=plan['max_part_bytes']:
                        break
                    target.unlink()
                    if count==1:
                        raise ValueError('Even one page exceeds configured upload byte budget')
                    count=max(1,count//2)
                with pymupdf.open(target) as part:
                    for index in range(part.page_count):
                        row=maps['preferred'][first+index]
                        expected=signatures[(row['source_document'],row['source_pdf_page'])]
                        if page_signature(part,index,image_cache)!=expected:
                            raise ValueError('Upload part content mismatch')
                products.append({'role':'upload_part','file':str(target.relative_to(destination)),
                                 'pages':count,'chapter_first_page':first+1,'chapter_last_page':last+1,
                                 'bytes':target.stat().st_size,'sha256':digest(target),'verified_against_source_pages':count})
                for i in range(count):
                    maps['preferred'][first+i].update(upload_part=target.name,upload_part_page=i+1)
                first=last+1
    for source in plan['documents'].values():
        if digest(ROOT/source['path'])!=source['sha256']:
            raise ValueError('Source PDF changed during export')
    write_json(destination/'page_map.json',{'kind':'derived_pdf_to_original_page_map','human_signoff':False,**maps})
    with (destination/'page_map.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(maps['preferred'][0]));writer.writeheader();writer.writerows(maps['preferred'])
    report={'kind':'verified_local_herb_chapter_export','status':'passed','plan_sha256':digest(plan_path),
            'source_documents':plan['documents'],'chapter_pages':plan['expected_pages'],
            'known_supplement_pages':[{'source':'full','pdf_page':76,'printed_page':28},
                                      {'source':'full','pdf_page':77,'printed_page':29}],
            'products':products,'page_map_sha256':digest(destination/'page_map.json'),
            'verification':'Every exported page: content stream hashes, image payload hashes/placements, media/crop geometry and rotation equal the selected source page.',
            'scan_resampling':False,'ocr_performed':False,'external_upload_performed':False,
            'printed_page_labels_fully_verified':False,'source_authenticity_verified':False,'human_signoff':False,
            'completeness_note':plan['completeness_note'],
            'cross_part_note':'Upload parts split pages, not monographs. Use page_map to restore chapter order and continue entries across part boundaries.'}
    write_json(destination/'export_report.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,default=ROOT/'eval/pharmacopoeia_herb_export_v1.json')
    parser.add_argument('--out',type=Path,default=ROOT/'var/pharmacopoeia_herbs_pdf')
    args=parser.parse_args();report=export(args.plan,args.out)
    print(json.dumps({'status':report['status'],'chapter_pages':report['chapter_pages'],
                      'files':[{'file':p['file'],'pages':p['pages'],'MiB':round(p['bytes']/1024/1024,1)} for p in report['products']]},ensure_ascii=False))


if __name__=='__main__':main()
