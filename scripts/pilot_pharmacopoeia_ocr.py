"""Local-only OCR pilot; optional dependencies stay outside the main application.

Raw OCR and page previews go to ignored var/. Source PDFs/database are read-only.
Manual regions and reference fragments are engineering candidates, not human signoff.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import socket
import statistics
import time
import unicodedata
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8*1024*1024), b''): h.update(block)
    return h.hexdigest()


def normalized(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, default=ROOT/'eval/pharmacopoeia_ocr_sample_v1.json')
    parser.add_argument('--out', type=Path, default=ROOT/'var/pharmacopoeia_ocr_sample/run')
    args = parser.parse_args()
    import importlib.metadata
    import numpy as np
    import pymupdf
    import onnxruntime
    onnxruntime.disable_telemetry_events()
    import rapidocr_onnxruntime
    from rapidocr_onnxruntime import RapidOCR

    raw = args.suite.read_bytes()
    suite = json.loads(raw)
    for source in suite['documents'].values():
        if digest(ROOT/source['path']) != source['sha256']: raise ValueError('Source PDF fingerprint changed')
    args.out.mkdir(parents=True, exist_ok=True)
    network = []
    def deny(*a, **kw):
        network.append('blocked')
        raise RuntimeError('No network permitted during local OCR')
    models = Path(rapidocr_onnxruntime.__file__).parent/'models'
    report = {'kind':suite['kind'],'review_status':suite['review_status'],'human_signoff':False,
              'suite_sha256':hashlib.sha256(raw).hexdigest(),
              'code_sha256':digest(Path(__file__)),
              'models':{p.name:digest(p) for p in models.glob('*.onnx')},
              'versions':{name:importlib.metadata.version(name) for name in ['rapidocr-onnxruntime','onnxruntime','PyMuPDF','numpy','opencv-python']},
              'settings':{'render_dpi':240,'column_split':.5,'threads':2,'det_limit_side_len':960,'onnx_telemetry':'disabled'},
              'pages':[],'cases':[],'source_pdf_authenticity_verified':False,
              'limitations':['Manual regions and reference fragments; no full-document extraction or clinical validation.',
                             'OCR confidence is model confidence, not empirical character accuracy.',
                             'No production ingestion or canonical Chinese entity mapping.']}
    rows = {}
    with patch.object(socket.socket,'connect',deny), patch.object(socket,'getaddrinfo',deny):
        ocr = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=2,
                       det_limit_side_len=960, max_side_len=4000)
        # A whole-page baseline exposes left/right interleaving before splitting columns.
        with pymupdf.open(ROOT/suite['documents']['high']['path']) as doc:
            page=doc[23]
            pix=page.get_pixmap(matrix=pymupdf.Matrix(240/72,240/72),alpha=False)
            array=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,pix.n)
            started=time.perf_counter()
            output,_=ocr(array)
            baseline=[]
            for box,text,score in output or []:
                baseline.append({'text':text,'score':float(score),'pixel_box':box,
                                 'column':'left' if sum(x for x,_ in box)/4<pix.width/2 else 'right'})
            switches=sum(a['column']!=b['column'] for a,b in zip(baseline,baseline[1:]))
            report['whole_page_baseline']={'document':'high','pdf_page':24,'printed_page':3,
                'line_count':len(baseline),'column_switches':switches,
                'elapsed_seconds':round(time.perf_counter()-started,3)}
            (args.out/'whole-page-baseline.json').write_text(json.dumps(baseline,ensure_ascii=False,indent=2)+'\n')
            print('whole-page baseline',len(baseline),'lines;',switches,'column switches',flush=True)
        pages = sorted({(s['document'],s['pdf_page']) for c in suite['cases'] for s in c['segments']} | {('full',51)})
        for label, number in pages:
            with pymupdf.open(ROOT/suite['documents'][label]['path']) as doc:
                page = doc[number-1]
                for column in ('left','right'):
                    clip = pymupdf.Rect(0 if column=='left' else page.rect.width/2,0,
                                        page.rect.width/2 if column=='left' else page.rect.width,page.rect.height)
                    pix = page.get_pixmap(matrix=pymupdf.Matrix(240/72,240/72),clip=clip,alpha=False)
                    array = np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,pix.n)
                    started = time.perf_counter()
                    output, elapsed = ocr(array)
                    lines=[]
                    for box,text,score in output or []:
                        pdf_box=[[float(x)*72/240+clip.x0,float(y)*72/240] for x,y in box]
                        lines.append({'text':text,'score':float(score),'pdf_box':pdf_box,
                                      'center_y_fraction':sum(y for _,y in pdf_box)/4/page.rect.height})
                    lines.sort(key=lambda line:min(y for _,y in line['pdf_box']))
                    key=f'{label}-{number}-{column}'
                    rows[key]=lines
                    record={'key':key,'source':suite['documents'][label],'pdf_page':number,'printed_page':suite['documents'][label]['printed_pages'][str(number)],'column':column,
                            'elapsed_seconds':round(time.perf_counter()-started,3),'line_count':len(lines),
                            'mean_model_confidence':statistics.mean(l['score'] for l in lines) if lines else None}
                    report['pages'].append(record)
                    (args.out/(key+'.json')).write_text(json.dumps({'metadata':record,'lines':lines},ensure_ascii=False,indent=2)+'\n')
                    print(key,len(lines),'lines',record['elapsed_seconds'],'seconds',flush=True)
        for case in suite['cases']:
            selected=[]
            for segment in case['segments']:
                key=f"{segment['document']}-{segment['pdf_page']}-{segment['column']}"
                for line in rows[key]:
                    if segment['y_range'][0] <= line['center_y_fraction'] <= segment['y_range'][1]:
                        selected.append({**line,'source_key':key})
            text='\n'.join(line['text'] for line in selected)
            checks=[]
            for field,expected in case['expected_fragments'].items():
                # Preserve the exact line boxes supporting each normalized match, including cross-line spans.
                target=normalized(expected); joined=''; offsets=[]
                for index,line in enumerate(selected):
                    value=normalized(line['text']);offsets.append((len(joined),len(joined)+len(value),index));joined+=value
                start=joined.find(target)
                evidence=[selected[i] for low,high,i in offsets if start>=0 and low<start+len(target) and high>start]
                checks.append({'field':field,'expected_fragment':expected,'matched':start>=0,
                               'supporting_lines':evidence})
            (args.out/(case['id']+'.txt')).write_text(text+'\n')
            report['cases'].append({'id':case['id'],'etcm_source_record_id':case['etcm_source_record_id'],
                                    'mapping_status':case['mapping_status'],'checks':checks,
                                    'matched':sum(c['matched'] for c in checks),'total':len(checks)})
    # Paired source comparison uses the same pre-recorded fragments, not new expected answers.
    paired=[]
    reference=suite['cases'][0]
    for label,number in [('high',24),('full',51)]:
        text=''.join(line['text'] for segment in reference['segments']
                     for line in rows[f"{label}-{number}-{segment['column']}"]
                     if segment['y_range'][0]<=line['center_y_fraction']<=segment['y_range'][1])
        paired.append({'document':label,'pdf_page':number,'printed_page':3,
                       'matched':sum(normalized(v) in normalized(text) for v in reference['expected_fragments'].values()),
                       'total':len(reference['expected_fragments'])})
    report['same_page_source_comparison']=paired
    report['network_attempts']=len(network)
    report['sources_unchanged']=all(digest(ROOT/d['path'])==d['sha256'] for d in suite['documents'].values())
    report['summary']={'herbs':len(report['cases']),'matched_fragments':sum(c['matched'] for c in report['cases']),
                       'total_fragments':sum(c['total'] for c in report['cases']),
                       'ocr_accuracy':None,'business_task_completion_rate':None}
    if args.suite.read_bytes()!=raw: raise RuntimeError('Reference suite changed')
    (args.out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report['summary']),flush=True)


if __name__=='__main__': main()
