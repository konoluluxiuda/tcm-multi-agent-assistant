"""Read-only bounded source audit. Run from the repository root; output contains schemas and aggregate counts, not source rows."""
import collections, datetime, hashlib, json, pathlib, sqlite3
root=pathlib.Path('/home/zry/workspace/HDCTI/dataset/ETCM2.0')
db=pathlib.Path('var/research_update_20261020/runtime_catalogs/catalogs/enrichment.sqlite3')
c=sqlite3.connect(f'file:{db}?mode=ro',uri=True)
result={'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'mode':'read_only','raw_root':str(root),'sidecar':str(db),'limitations':['Sidecar node groups are not verified unique formulas.','Ingredient sampling is bounded and not a prevalence estimate.','Filename matching is only a name candidate, not entity validation.'],'raw_inventory':{}}
files={}
for directory in sorted(root.iterdir()):
 if directory.is_dir():
  rows=sorted((p.stat().st_size,p.name,p) for p in directory.glob('*.json'))
  files[directory.name]=rows
  result['raw_inventory'][directory.name]={'json_files':len(rows),'bytes':sum(x[0] for x in rows)}
result['sidecar_metadata']={k:v for k,v in c.execute('select key,value from metadata') if k in ('kind','build_complete','dataset_version','base_database_sha256','source_fingerprint','relations_are_source_assertions')}
result['edges']=[dict(zip(['kind','rows','herb_sources','node_groups'],r)) for r in c.execute('select kind,count(*),count(distinct herb_id),count(distinct node_id) from edges group by kind')]
herb_names={p.stem.casefold().replace(' ','') for _,_,p in files['etcm_herbs']}
result['formula_details']={}
for kind in ('traditional_chinese_medicine_formula','chinese_patent_drug'):
 keys=collections.Counter(); nonempty=collections.Counter(); cap=collections.Counter(); variants=collections.defaultdict(set); components=collections.Counter(); n=0
 for node,raw,status in c.execute('select node_id,raw_row,capture_status from edges where kind=?',(kind,)):
  row=json.loads(raw);n+=1; keys.update(row.keys());nonempty.update(k for k,v in row.items() if v not in ('',None,[],{}))
  payload={k:v for k,v in row.items() if k!='linkformat'}
  variants[node].add(hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest())
  for item in row.get('Herbs Contained in This Formula (Chinese Pinyin)',[]):
   if isinstance(item,str):components[item]+=1
  for item in json.loads(status):cap[item.get('status','unspecified')]+=1
 result['formula_details'][kind]={'rows':n,'keys':dict(keys),'nonempty_fields':dict(nonempty),'node_groups':len(variants),'groups_with_distinct_payloads':sum(len(v)>1 for v in variants.values()),'distinct_payloads_per_group_total':sum(map(len,variants.values())),'capture_status_items':dict(cap),'distinct_component_labels':len(components),'component_labels_with_case_space_only_filename_candidate':sum(s.casefold().replace(' ','') in herb_names for s in components),'component_occurrences':sum(components.values()),'component_occurrences_with_case_space_only_filename_candidate':sum(v for s,v in components.items() if s.casefold().replace(' ','') in herb_names)}
def sections(obj):
 if isinstance(obj,dict):
  if obj.get('id') in ('related_table','base_information','Enrichment'):yield obj
  for v in obj.values():yield from sections(v)
 elif isinstance(obj,list):
  for v in obj:yield from sections(v)
def schema(obj):
 if isinstance(obj,dict):return {k:(list(v)[:20] if isinstance(v,dict) else 'list:'+str(len(v)) if isinstance(v,list) else type(v).__name__) for k,v in obj.items()}
 return type(obj).__name__
# Even positions across size-sorted eligible files: not a random population sample.
elig=[r for r in files['etcm_ingredients'] if r[0]<=8*1024*1024]
indices=sorted({round(i*(len(elig)-1)/11) for i in range(12)})
result['ingredient_sample']={'selection':'12 evenly spaced indices of size-sorted JSON files <=8 MiB','eligible_files':len(elig),'population_files':len(files['etcm_ingredients']),'records':[]}
for i in indices:
 size,name,p=elig[i];b=p.read_bytes();obj=json.loads(b)
 entry={'file':name,'bytes':size,'sha256':hashlib.sha256(b).hexdigest(),'root_keys':list(obj),'source_status':{'code':obj.get('code'),'msg':obj.get('msg')},'sections':[]}
 for sec in sections(obj):
  entry['sections'].append({'type':sec['id'],'schema':schema(sec),'value_schema':schema(sec.get('value')),'tables':[{'type':child.get('type'),'label':child.get('label'),'declared_count':child.get('count'),'rows':len(child.get('value',[])) if isinstance(child.get('value'),list) else None,'row_keys':sorted({k for row in child.get('value',[]) if isinstance(row,dict) for k in row}) if isinstance(child.get('value'),list) else []} for child in sec.get('value',[]) if isinstance(child,dict)]})
 result['ingredient_sample']['records'].append(entry)
# Schema example for disease enrichment, not a full disease audit.
p=root/'etcm_diseases'/'Dermatosis_Papulosa_Nigra.json'
obj=json.loads(p.read_bytes());result['disease_enrichment_sample']={'file':p.name,'sections':[{'type':s['id'],'schema':schema(s),'value_schema':schema(s.get('value')),'table_ids':[child.get('id') for child in s.get('value',[]) if isinstance(child,dict)]} for s in sections(obj) if s['id']=='Enrichment']}
path=pathlib.Path('audits/research_requirements_v1/data_readiness.json')
path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print('Formula-related rows audited:',sum(v['rows'] for v in result['formula_details'].values()))
print('saved',path)
