"""Check query delivery across direct and queued answers, using raw JSON witnesses.

No routing/model code is used as the oracle. PDF evidence is checked for anchors,
not certified for OCR correctness or medical validity.
"""
import json
from pathlib import Path
import re

from scripts.eval_user_chat_replay import original_pointer

LABELS={'family':'科','medicinal_part':'药用部位','properties':'药性',
        'flavors':'药味','meridians':'归经','habitat':'产地'}


def validate(expect):
    if not isinstance(expect,dict) or not expect or set(expect)-{'attributes','pharmacopoeia','relations'}:
        raise ValueError('Unsupported answer contract')
    if not any(expect.values()):raise ValueError('Empty answer contract')
    for key,requests in expect.items():
        if not isinstance(requests,list) or not requests:raise ValueError('Answer requirements must be nonempty lists')
        for request in requests:
            if key=='attributes':
                if set(request)!={'source','fields'} or not request['source'] or not request['fields'] or set(request['fields'])-set(LABELS):
                    raise ValueError('Invalid attribute requirement')
            elif key=='pharmacopoeia':
                if set(request)!={'name','fields'} or not request['name'] or not request['fields'] or any(not isinstance(f,str) or not f for f in request['fields']):
                    raise ValueError('Invalid pharmacopoeia requirement')
            elif (set(request)!={'kind','sources'} or request['kind'] not in ('ingredient','target','common_target')
                  or len(request['sources'])!=(2 if request['kind']=='common_target' else 1)
                  or len(set(request['sources']))!=len(request['sources'])):raise ValueError('Invalid relation requirement')


def objects(value):
    if isinstance(value,dict):
        yield value
        for child in value.values():yield from objects(child)
    elif isinstance(value,list):
        for child in value:yield from objects(child)


def attributes(value,expect,source):
    filename='etcm_herbs/'+expect['source']+'.json';found={};checked=0
    for item in objects(value):
        fields=item.get('fields',{})
        if isinstance(fields,dict):
            for field in expect['fields']:
                cell=fields.get(field,{})
                proof=cell.get('source') or {}
                if proof.get('relative_file_path')==filename and cell.get('state')=='present':
                    original_pointer(source,proof)
                    if cell.get('value')!=proof['raw_value']:raise ValueError('Attribute value differs from raw witness')
                    found[field]=True;checked+=1
        if item.get('tool')=='herb_attributes':
            rows=item.get('field_rows',[])
            full=(item.get('data') or {}).get('report') or {}
            if full.get('rows'):
                rows=[{'field':r['label'],'original_value':r.get('original_value'),'evidence':r.get('source')} for r in full['rows']]
            for row in rows:
                proof=row.get('evidence') or {}
                if proof.get('relative_file_path')!=filename:continue
                for field in expect['fields']:
                    if row.get('field')==LABELS[field]:
                        original_pointer(source,proof)
                        if row.get('original_value')!=proof['raw_value']:raise ValueError('Attribute original value changed')
                        found[field]=True;checked+=1
    if set(found)!=set(expect['fields']):raise ValueError('Requested attributes or raw witnesses missing')
    return checked


def pharmacopoeia(value,expect):
    found=set();checked=0
    def accept(label,proof):
        nonlocal checked
        if label not in expect['fields']:return
        if not proof or any(not p.get('block_id') or type(p.get('source',{}).get('source_pdf_page')) is not int
                            or p['source']['source_pdf_page']<1 or not re.fullmatch('[0-9a-f]{64}',p['source'].get('source_sha256','')) for p in proof):
            raise ValueError('Pharmacopoeia field lacks page/block/source hash')
        found.add(label);checked+=len(proof)
    for item in objects(value):
        if item.get('name')==expect['name'] and item.get('text'):
            accept(item.get('label'),item.get('evidence'))
        if item.get('tool')=='pharmacopoeia_lookup' and item.get('title')==expect['name']+' · 药典':
            for row in item.get('field_rows',[]):
                if row.get('value'):accept(row.get('field'),row.get('evidence'))
    if found!=set(expect['fields']):raise ValueError('Requested pharmacopoeia fields or page evidence missing')
    return checked


def raw_rows(document,kind):
    return {f'/data/{i}/value/{j}/value/{k}':row
            for i,section in enumerate(document.get('data',[])) if section.get('id')=='related_table'
            for j,table in enumerate(section.get('value',[])) if table.get('type')==kind
            for k,row in enumerate(table.get('value',[]))}


def identifiers(row):
    value=row.get('UniProt Accession Number',[])
    return set(value if isinstance(value,list) else [value])


def relation(value,expect,source):
    names=set(expect['sources']);kind=expect['kind'];rawkind='target' if kind=='common_target' else kind
    docs={name:json.loads((source/'etcm_herbs'/(name+'.json')).read_text()) for name in names}
    rows={name:raw_rows(doc,rawkind) for name,doc in docs.items()}
    target_sets=[set().union(*(identifiers(r) for r in items.values())) for items in rows.values()]
    expected=len(set.intersection(*target_sets)) if kind=='common_target' else len(next(iter(rows.values())))
    checked=0
    def witness(proof,raw,filename,identifier=None):
        nonlocal checked
        name=Path(filename).stem
        pointer=proof['json_pointer']
        if pointer not in rows[name] or rows[name][pointer]!=raw:raise ValueError('Witness does not belong to the requested relation table')
        original_pointer(source,{**proof,'raw_value':raw})
        if identifier is not None and identifier not in identifiers(raw):raise ValueError('Shared identifier absent from a witness')
        checked+=1
    for item in objects(value):
        displayed=item.get('tool')=='herb_relation_records' and item.get('report_kind')=='relations'
        if displayed:
            if kind=='common_target' or not item.get('title','').endswith(' · '+{'ingredient':'成分','target':'靶点'}[kind]):continue
            groups=item.get('source',{}).get('groups',[]);data={'total':item.get('total'),'items':item.get('field_rows',[])}
        elif item.get('kind')==kind and isinstance(item.get('data'),dict):groups=item.get('groups',[]);data=item['data']
        else:continue
        options=[g.get('options',[]) for g in groups]
        if not options or any(len(o)!=1 for o in options):continue
        selected=[o[0] for o in options]
        if {Path(o.get('source',{}).get('relative_file_path','')).stem for o in selected}!=names:continue
        if data.get('total')!=expected:raise ValueError('Relation count differs from original JSON')
        entries=data.get('items',[])
        if len(entries)!=min(20,expected):raise ValueError('First relation page is incomplete')
        keys=[e['identifier'] if kind=='common_target' else
              e['evidence']['json_pointer'] if displayed else e['source_pointer'] for e in entries]
        if len(set(keys))!=len(keys):raise ValueError('Relation page repeats the same result')
        by_id={o['source_record_id']:o['source'] for o in selected}
        for entry in entries:
            if kind=='common_target':
                if len(entry.get('sources',[]))!=len(names) or {s['source_record_id'] for s in entry['sources']}!=set(by_id):
                    raise ValueError('Shared identifier lacks both sources')
                for side in entry['sources']:
                    if not side.get('witnesses'):raise ValueError('Shared identifier lacks witnesses')
                    info=by_id[side['source_record_id']]
                    for w in side['witnesses']:
                        witness({**info,'json_pointer':w['source_pointer']},w['raw_row'],info['relative_file_path'],entry['identifier'])
            elif displayed:
                proof=entry['evidence'];info=by_id[proof['source_record_id']]
                witness({**info,'json_pointer':proof['json_pointer']},proof['raw_row'],info['relative_file_path'])
            else:
                info=selected[0]['source']
                witness({**info,'json_pointer':entry['source_pointer']},entry['raw_row'],info['relative_file_path'])
        return checked
    raise ValueError('Requested relation/source result missing')


def check(value,expect,source):
    validate(expect)
    source=Path(source);checked=0
    for request in expect.get('attributes',[]):checked+=attributes(value,request,source)
    for request in expect.get('pharmacopoeia',[]):checked+=pharmacopoeia(value,request)
    for request in expect.get('relations',[]):checked+=relation(value,request,source)
    return {'raw_witnesses_or_pdf_anchors_checked':checked,
            'scope':'ETCM raw JSON equality and PDF anchor structure; not medical or OCR correctness'}
