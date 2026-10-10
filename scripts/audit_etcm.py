"""Read-only ETCM response audit. Full herbs/targets; samples elsewhere.

Only summary/metadata artifacts are written to --out; no source modification,
network calls, embedding calls, or database ingestion.
"""
import argparse
import csv
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


def blank(value):
    return value is None or value == '' or value == [] or value == {}


def non_json_constant(value):
    raise ValueError('Non-JSON numeric constant: '+value)


def audit_group(files, selected, root):
    fields = defaultdict(lambda: {'records_present': 0, 'nonempty': 0, 'types': Counter(), 'html_records': 0, 'examples': []})
    sections = Counter()
    table_stats = defaultdict(lambda: {'records':0,'rows':0,'max_rows':0,'row_fields':Counter()})
    codes, top_fields, data_types, summary_statuses = Counter(), Counter(), Counter(), Counter()
    names, hashes = defaultdict(list), defaultdict(list)
    issues, manifest = [], []
    successful = valid_basic = names_with_cjk = 0
    basic_layouts = Counter()
    examples = []
    for number, p in enumerate(selected):
        relative=str(p.relative_to(root))
        raw=p.read_bytes()
        digest=hashlib.sha256(raw).hexdigest()
        hashes[digest].append(relative)
        entry={'path':relative,'bytes':len(raw),'sha256':digest}
        manifest.append(entry)
        try:
            obj=json.loads(raw,parse_constant=non_json_constant)
        except (ValueError,UnicodeError) as exc:
            issues.append({'file':relative,'kind':'invalid_json','detail':str(exc)[:160]})
            continue
        if not isinstance(obj,dict):
            issues.append({'file':relative,'kind':'non_object_root'});continue
        successful+=1
        codes[str(obj.get('code'))]+=1
        top_fields.update(obj.keys())
        data=obj.get('data')
        data_types[type(data).__name__]+=1
        if obj.get('code') != 1:
            issues.append({'file':relative,'kind':'api_non_success','code':obj.get('code'),'msg':str(obj.get('msg'))[:160]})
        if not isinstance(data,list):
            issues.append({'file':relative,'kind':'data_not_list'});continue
        basic=[s for s in data if isinstance(s,dict) and s.get('id')=='base_information']
        items = []
        if len(basic) == 1 and isinstance(basic[0].get('value'), list):
            items = basic[0]['value']
            basic_layouts['flat'] += 1
        elif len(basic) == 1 and isinstance(basic[0].get('children'), list) and basic[0]['children']:
            basic_layouts['nested_children'] += 1
            def walk(node, prefix=''):
                part = prefix + str(node.get('id', node.get('key', 'unknown'))) + '/'
                if isinstance(node.get('value'), list):
                    for item in node['value']:
                        if isinstance(item, dict) and isinstance(item.get('key'), str):
                            items.append({**item, 'key':part + item['key']})
                for child in node.get('children', []):
                    if isinstance(child, dict): walk(child, part)
            for child in basic[0]['children']:
                if isinstance(child, dict): walk(child)
        else:
            issues.append({'file':relative,'kind':'basic_information_missing_or_wrong_type'})
        if items:
            valid_basic+=1
            seen=set()
            basic_values={}
            for item in items:
                if not isinstance(item,dict) or not isinstance(item.get('key'),str):
                    issues.append({'file':relative,'kind':'malformed_basic_field'});continue
                key=item['key'];value=item.get('value')
                if key in seen:
                    issues.append({'file':relative,'kind':'duplicate_basic_key','key':key});continue
                seen.add(key);basic_values[key]=value
                stats=fields[key];stats['records_present']+=1
                stats['nonempty']+=not blank(value)
                stats['types'][type(value).__name__]+=1
                stats['html_records']+=isinstance(value,str) and bool(re.search(r'<[^>]+>',value))
                if not blank(value) and len(stats['examples'])<2:
                    stats['examples'].append({'file':relative,'value_preview':str(value)[:180]})
            name=basic_values.get('Herb Name in Pinyin') or basic_values.get('Ingredient Name') or basic_values.get('Target Name') or basic_values.get('Disease Name') or obj.get('name')
            if isinstance(name,str):
                names[name.strip().casefold()].append(relative)
                normalize = lambda v: re.sub(r'[\W_]+', '_', v.casefold()).strip('_')
                if normalize(name) != normalize(p.stem):
                    issues.append({'file':relative,'kind':'filename_vs_primary_name', 'primary_name':name})
                names_with_cjk+=bool(re.search('[\u4e00-\u9fff]',name))
            if len(examples)<2 or p.stem.lower()=='huangqin':
                examples.append({'file':relative,'top_name':obj.get('name'),'page_title':obj.get('page_title'),
                                 'basic_fields':basic_values})
        actual_tables={}
        for section in data:
            if not isinstance(section,dict):continue
            sid=section.get('id',section.get('key','unknown'));sections[str(sid)]+=1
            if sid!='related_table' or not isinstance(section.get('value'),list):continue
            for table in section['value']:
                if not isinstance(table,dict):continue
                key=str(table.get('type') or table.get('label'))
                rows=table.get('value')
                if not isinstance(rows,list):
                    issues.append({'file':relative,'kind':'table_not_list','table':key});continue
                ts=table_stats[key];ts['records']+=1;ts['rows']+=len(rows);ts['max_rows']=max(ts['max_rows'],len(rows))
                actual_tables[key]=len(rows)
                # The row-field catalogue is sampled within tables; counts are full.
                for row in rows[:3]:
                    if isinstance(row,dict):ts['row_fields'].update(row.keys())
        for summary in obj.get('complete_fetch_summary',[]) or []:
            if not isinstance(summary,dict):continue
            summary_statuses[str(summary.get('status'))]+=1
            if summary.get('expected')!=summary.get('actual'):
                issues.append({'file':relative,'kind':'fetch_count_mismatch','summary':summary})
            if summary.get('section')=='related_table' and summary.get('type') in actual_tables:
                actual=actual_tables[summary['type']]
                if summary.get('actual')!=actual:
                    issues.append({'file':relative,'kind':'summary_vs_table_count_mismatch','table':summary['type'],'summary_actual':summary.get('actual'),'rows':actual})
        if (number+1)%100==0: print(f'{p.parent.name}: {number+1}/{len(selected)}',flush=True)
    for v in fields.values():
        v['records_absent']=len(selected)-v['records_present']
        v['nonempty_fraction_of_scanned_files']=round(v['nonempty']/len(selected),4) if selected else None
    sizes=sorted(p.stat().st_size for p in files)
    result={'inventory_files':len(files),'inventory_bytes':sum(sizes),
            'largest_file_bytes':max(sizes,default=0),'scanned_files':len(selected),
            'scan_scope':'full' if len(selected)==len(files) else 'sample_only',
            'parsed_object_files':successful,'valid_basic_information_files':valid_basic,'basic_layouts':basic_layouts,
            'primary_names_containing_cjk':names_with_cjk,'api_codes':codes,'data_types':data_types,
            'top_fields':top_fields,'basic_fields':dict(fields),'sections':sections,
            'related_tables':dict(table_stats),'fetch_summary_status_counts':summary_statuses,
            'duplicate_casefolded_primary_names':{k:v for k,v in names.items() if len(v)>1},
            'identical_content_files':[v for v in hashes.values() if len(v)>1],
            'issues':issues,'issue_counts':Counter(x['kind'] for x in issues),'examples':examples}
    return result,manifest


def review_candidates(report):
    candidates = defaultdict(list)
    for group in report['groups'].values():
        for issue in group['issues']:
            candidates[issue['file']].append({k:v for k,v in issue.items() if k != 'file'})
        for paths in group['identical_content_files']:
            for path in paths:
                candidates[path].append({'kind':'identical_content_group', 'files':paths})
        for name, paths in group['duplicate_casefolded_primary_names'].items():
            for path in paths:
                candidates[path].append({'kind':'duplicate_primary_name', 'normalized_name':name, 'files':paths})
    return {
        'source':report['source'], 'audit_created_at':report['created_at'],
        'action':'Review candidates only; source files were not moved, deleted, or modified. Warnings do not prove every candidate is invalid.',
        'files':[{'file':p, 'status':'needs_review', 'reasons':r} for p,r in sorted(candidates.items())],
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--out',type=Path,default=Path('audits/etcm2'))
    parser.add_argument('--sample-size',type=int,default=24)
    parser.add_argument('--full-groups',nargs='+',default=['etcm_herbs','etcm_targets'])
    args=parser.parse_args()
    if args.sample_size<1:parser.error('--sample-size must be positive')
    root=args.source.resolve()
    if not root.is_dir():parser.error('--source must be an existing directory')
    if args.out.resolve().is_relative_to(root):parser.error('--out must be outside the read-only source directory')
    args.out.mkdir(parents=True,exist_ok=True)
    rng=random.Random(42)
    report={'created_at':datetime.now(timezone.utc).isoformat(),'source':str(root),
            'method':'Full scan of selected full_groups; other groups: seeded random sample plus smallest/largest/median file. Inventory counts all JSON files. Table row-field names only sample first 3 rows per table.',
            'full_groups':args.full_groups,
            'groups':{}}
    inventory=[];scanned=[]
    for directory in sorted(root.iterdir()):
        if not directory.is_dir():continue
        files=sorted(directory.glob('*.json'))
        if not files:continue
        by_size=sorted(files,key=lambda p:(p.stat().st_size,p.name))
        selected=files if directory.name in args.full_groups else sorted(set(rng.sample(files,min(args.sample_size,len(files)))+[by_size[0],by_size[-1],by_size[len(by_size)//2]]))
        print('Scanning',directory.name,len(selected),'of',len(files),flush=True)
        group,entries=audit_group(files,selected,root)
        report['groups'][directory.name]=group;scanned.extend(entries)
        for p in files:
            st=p.stat();inventory.append({'path':str(p.relative_to(root)),'bytes':st.st_size,'mtime_ns':st.st_mtime_ns})
    (args.out/'audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (args.out/'scanned_sha256.json').write_text(json.dumps(scanned,ensure_ascii=False,indent=2),encoding='utf-8')
    (args.out/'quarantine_candidates.json').write_text(json.dumps(review_candidates(report),ensure_ascii=False,indent=2),encoding='utf-8')
    with (args.out/'inventory.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=['path','bytes','mtime_ns'],lineterminator='\n');writer.writeheader();writer.writerows(inventory)
    print('Done. JSON files inventoried:',len(inventory),'Content-scanned:',len(scanned),flush=True)


if __name__=='__main__':main()
