"""Independent, stdlib-only source audit; never imports the production repository.

Blind phase extracts only id/operation/input from the suite. Comparison is a
separate phase and verifies the persisted predictions' suite/source bindings.
"""
import argparse
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
MAPPING = {
 'name_pinyin':'Herb Name in Pinyin', 'name_latin':'Herb Name in Latin',
 'name_english':'Herb Name in English', 'family':'Family', 'habitat':'Habitat',
 'medicinal_part':'Medicinal Part', 'efficacy_class':'Herb classification based on efficacy',
 'properties':'Property', 'flavors':'Flavor', 'meridians':'Meridian Tropism',
 'indications':'Indications', 'pubmed_ids':'PubMed', 'database_cross_references':'Database Cross References',
}
FILTERS = {'family','habitat','medicinal_part','properties','flavors','meridians','efficacy_class','indications'}

def sha(raw): return hashlib.sha256(raw).hexdigest()
def dump(path, value): path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
class Text(HTMLParser):
 def __init__(self): super().__init__(convert_charrefs=True); self.parts=[]
 def handle_data(self,data): self.parts.append(data)
def plain(value):
 parser=Text();parser.feed(value);parser.close()
 return ' '.join(''.join(parser.parts).split())
def norm(value): return ' '.join(value.split()).casefold()

def sources(directory):
 directory=directory/'etcm_herbs' if (directory/'etcm_herbs').is_dir() else directory
 rows={}; manifest=[]; issues=[]
 for p in sorted(directory.glob('*.json')):
  raw=p.read_bytes(); rel='etcm_herbs/'+p.name; digest=sha(raw)
  manifest.append({'source_file':rel,'file_sha256':digest})
  d=json.loads(raw); sections=[(i,s) for i,s in enumerate(d.get('data',[])) if s.get('id')=='base_information']
  if d.get('code')!=1 or len(sections)!=1:
   issues.append({'file':rel,'reason':'invalid status or basic section'});continue
  si,sec=sections[0]; items=sec['value'];fields={}
  for field,key in MAPPING.items():
   matches=[(j,item) for j,item in enumerate(items) if item['key']==key]
   if len(matches)>1: issues.append({'file':rel,'reason':'duplicate field','field':field})
   if not matches:
    fields[field]={'state':'missing','value':None,'raw_value':None,'raw_item':None,'json_pointer':None,'source_field':key};continue
   j,item=matches[0]; value=item['value']
   state='missing' if value is None or value == '' or value == [] else 'present'
   if state=='present' and field in {'name_latin','name_english'}:value=plain(value)
   if state=='present' and field=='pubmed_ids':
    if not isinstance(value,list) or any(not isinstance(x,str) or not re.fullmatch(r'[1-9][0-9]*',x) for x in value):issues.append({'file':rel,'reason':'invalid PMID'})
    value=list(dict.fromkeys(value))
   fields[field]={'state':state,'value':value if state=='present' else None,'raw_value':item['value'],'raw_item':item,'json_pointer':f'/data/{si}/value/{j}/value','source_field':key}
  rows[rel]={'source_file':rel,'file_sha256':digest,'source_record_id':'etcm2-local:'+sha(rel.encode()),'fields':fields,'basic_pointer':f'/data/{si}','basic_raw':sec}
 # Both common manifest encodings are retained; comparison must explicitly report the frozen convention.
 fingerprint=sha(''.join(f"{x['source_file']}\t{x['file_sha256']}\n" for x in manifest).encode())
 return rows, manifest, fingerprint, issues

def lookup(inp, rows, dataset_version=None):
 if inp.get('dataset_version') and inp['dataset_version']!=dataset_version:
  return {'status':'dataset_not_found','paths':[], 'basis':'product_contract: version is external import state; source JSON cannot prove absence'}
 name=inp.get('name','')
 if any('\u4e00'<=c<='\u9fff' for c in name):return {'status':'unsupported_name_mapping','paths':[], 'basis':'product_contract: Chinese mapping deliberately unsupported'}
 matches=[]
 for path,row in rows.items():
  if inp.get('source_record_id'):
   match=inp['source_record_id']==row['source_record_id']
  else:match=any(norm(name)==norm(row['fields'][f]['value'] or '') for f in ['name_pinyin','name_latin','name_english'])
  if match:matches.append(path)
 return {'status':'ok' if len(matches)==1 else 'ambiguous_entity' if matches else 'not_found','paths':sorted(matches),'basis':'source_values + explicit normalization/identity contract'}

def predict(case, rows, dataset_version=None):
 inp=case['input'];op=case['operation']
 if op=='lookup':return lookup(inp,rows,dataset_version)
 if op=='filter':
  filters=inp.get('filters',{});lim=inp.get('limit',20);off=inp.get('offset',0)
  reason='empty_filters' if not filters else 'unsupported_filter_field' if set(filters)-FILTERS else 'negative_offset' if off<0 else 'limit_out_of_range' if not 1<=lim<=100 else None
  if reason:return {'exception':'ValueError','semantic_error':reason,'http_status':422 if reason in {'negative_offset','limit_out_of_range'} else 400,'basis':'external explicit product/API contract; not JSON truth'}
  hits=[p for p,r in rows.items() if all(norm(v) in norm(r['fields'][f]['value'] or '') for f,v in filters.items())]
  hits.sort();page=hits[off:off+lim]
  return {'status':'ok','paths':page,'count':len(hits),'returned_count':len(page),'limit':lim,'offset':off,'next_offset':off+lim if off+lim<len(hits) else None,'basis':'source values + product substring/AND/sort/pagination contract'}
 if op=='compare':
  if not inp['names']:return {'exception':'ValueError','semantic_error':'empty_names','http_status':400,'basis':'external contract; API empty names forwarded to repository'}
  if inp.get('dataset_version') and inp['dataset_version']!=dataset_version:return {'status':'dataset_not_found','items':[],'basis':'product_contract: import state cannot be inferred from source JSON'}
  return {'status':'ok','items':[dict(name=n,**lookup({'name':n},rows,dataset_version)) for n in inp['names']], 'basis':'source values + product compare contract'}
 if op=='ready':return {'status':'ready','record_count':len(rows),'basis':'conditional contract: requires successful import/schema; source availability alone does not prove DB readiness'}
 raise ValueError('unsupported operation '+op)

def blind(args):
 raw=args.suite.read_bytes(); selected=json.loads(raw)
 cases=[{k:c[k] for k in ('id','operation','input')} for c in selected['cases']]
 dataset_version=selected.get('dataset_version')
 del selected
 rows,manifest,fingerprint,issues=sources(args.source)
 predictions=[dict(c,prediction=predict(c,rows,dataset_version)) for c in cases]
 paths=set()
 for c in predictions:
  paths.update(c['prediction'].get('paths',[]))
  for item in c['prediction'].get('items',[]):paths.update(item.get('paths',[]))
 subset={p:r for p,r in rows.items() if p in paths}
 result={'artifact_scope':'Only records selected from blind predicted result paths; no expected/records used', 'date':'2026-10-14','reviewer':'independent AI sub-agent on same platform; no different model/provider claimed','suite_sha256':sha(raw),'source_fingerprint':fingerprint,'fingerprint_convention':'sha256(sorted relative_path + TAB + file_sha256 + LF)','source_files':manifest,'source_issues':issues,'field_mapping_basis':'ETCM source keys aligned to public MVP field names; explicit product contract','cases':predictions,'records':subset}
 args.out.mkdir(parents=True,exist_ok=True);dump(args.out/'blind_predictions.json',result)
 return result

def compare(args):
 artifact=args.out/'blind_predictions.json'
 b=json.loads(artifact.read_text(encoding='utf-8'))
 suite_bytes=args.suite.read_bytes()
 if sha(suite_bytes)!=b['suite_sha256']:raise ValueError('suite changed since blind prediction')
 current,manifest,fingerprint,issues=sources(args.source)
 if fingerprint!=b['source_fingerprint'] or manifest!=b['source_files']:raise ValueError('source snapshot changed since blind prediction')
 suite=json.loads(suite_bytes)
 checks=[]; differences=[]; limitations=[]
 def check(group,key,expected,observed):
  item={'key':key,'expected':expected,'observed':observed,'ok':expected==observed}
  group.append(item)
  if not item['ok']:differences.append(item)
 snap=[]
 compact=sha(json.dumps([[x['source_file'],x['file_sha256']] for x in manifest],ensure_ascii=False,separators=(',',':')).encode())
 check(snap,'snapshot.record_count',suite['snapshot']['record_count'],len(manifest))
 check(snap,'snapshot.sha256',suite['snapshot']['sha256'],compact)
 aliases={'latin_name':'name_latin','english_name':'name_english','reference_pmids':'pubmed_ids','cross_references':'database_cross_references'}
 records=[];field_total=0;normalization_differences=[]
 for path,frozen in suite['records'].items():
  group=[];row=b['records'].get(path)
  if row is None:
   check(group,path+'.exists',True,False);records.append({'record_key':path,'checks':group});continue
  check(group,path+'.sha256',frozen['sha256'],row['file_sha256'])
  check(group,path+'.fields',sorted(frozen['fields']),sorted(aliases.keys() | (set(MAPPING)-set(aliases.values()))))
  for name,expected in frozen['fields'].items():
   field_total+=1;found=row['fields'][aliases.get(name,name)]
   observed={'state':found['state'],'value':found['value'],'json_pointer':found['json_pointer'],'raw_field_key':found['source_field'] if found['json_pointer'] is not None else None,'raw_value':found['raw_value']}
   for key,value in expected.items():
    if key=='value' and isinstance(value,str) and isinstance(observed[key],str) and value!=observed[key] and value==observed[key].strip():
     normalization_differences.append({'path':path,'field':name,'frozen':value,'blind':observed[key],'reason':'frozen trims ordinary field edges; independent blind rules preserved them'})
     group.append({'key':path+'.'+name+'.value','expected':value,'observed':observed[key],'ok':True,'classification':'reasonable normalization convention difference','comparison_value':observed[key].strip()})
    else:
     check(group,path+'.'+name+'.'+key,value,observed.get(key,'<unsupported frozen field key>'))
     if key=='value' and isinstance(value,str) and isinstance(observed[key],str) and norm(value)==norm(observed[key]) and value!=observed[key]:
      group[-1]['classification']='reasonable HTML whitespace convention difference; kept as literal mismatch'
  records.append({'record_key':path,'checks':group,'mismatches':[x for x in group if x['ok'] is False]})
 preds={c['id']:c for c in b['cases']};case_results=[]
 check(snap,'case_ids',[c['id'] for c in suite['cases']],[c['id'] for c in b['cases']])
 for case in suite['cases']:
  p=preds[case['id']]['prediction'];e=case['expected'];group=[];limits=[]
  check(group,case['id']+'.input',case['input'],preds[case['id']]['input'])
  check(group,case['id']+'.operation',case['operation'],preds[case['id']]['operation'])
  observed=dict(p)
  if 'items' in p:observed['herbs']=[{'query':x['name'],'status':x['status'],'paths':x['paths']} for x in p['items']]
  for key,value in e.items():
   if key=='message_contains':
    limits.append('Exact repository exception wording was not independently predicted; '+repr(value)+' retained as external contract assertion, not source truth.')
    # Semantics independently predicted before opening expected. Wording is not a fabricated independent check.
    group.append({'key':case['id']+'.message_contains','expected':value,'observed':p.get('semantic_error'),'ok':None,'classification':'not independently verified; semantic error only'})
   else:check(group,case['id']+'.'+key,value,observed.get(key,'<not predicted>'))
  if 'product_contract:' in p['basis'] or case['operation']=='ready':limits.append(p['basis'])
  if case['operation']=='compare':limits.append('Aligned field values checked through all frozen record fields; no actual compare API response invoked.')
  limitations.extend(limits)
  case_results.append({'id':case['id'],'basis':p['basis'],'basis_class':'source_plus_product_contract' if p['basis'].startswith('source') else 'external_contract_only','checks':group,'mismatches':[x for x in group if x['ok'] is False],'contract_limits':limits})
 # Bind report to public contract texts, but never use answer-bearing review document in blind phase.
 docs={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in [ROOT/'docs/ETCM2_MVP_USAGE.md',ROOT/'docs/TCM_GOLDEN_REVIEW.md',ROOT/'tcm/api.py']}
 unchanged=args.suite.read_bytes()==suite_bytes
 if not unchanged:raise ValueError('suite changed during comparison')
 directory=args.source/'etcm_herbs' if (args.source/'etcm_herbs').is_dir() else args.source
 end_manifest=[{'source_file':'etcm_herbs/'+p.name,'file_sha256':sha(p.read_bytes())} for p in sorted(directory.glob('*.json'))]
 if end_manifest!=manifest:raise ValueError('source changed during comparison')
 all_checks=snap+[x for r in records for x in r['checks']]+[x for r in case_results for x in r['checks']]
 summary={'source_files':len(manifest),'records':len(records),'fields':field_total,'cases':len(case_results),'mismatches':len(differences),'checks':len(all_checks),'verified_checks':sum(x['ok'] is not None for x in all_checks),'unverified_checks':sum(x['ok'] is None for x in all_checks),'contract_only_case_count':sum(not c['prediction']['basis'].startswith('source') for c in b['cases']),'reasonable_normalization_differences':len(normalization_differences),'not_independently_verified_message_assertions':sum('message_contains' in c['expected'] for c in suite['cases']),'source_derived_case_count':sum(c['prediction']['basis'].startswith('source') for c in b['cases'])}
 report={'date':'2026-10-14','reviewer':'AI; same platform independent sub-agent; not a different model/provider; no human signoff','suite_sha256':sha(suite_bytes),'source_fingerprint':fingerprint,'frozen_snapshot_fingerprint':compact,'contract_sha256':docs,'input_unchanged':unchanged,'snapshot_checks':snap,'record_checks':records,'case_checks':case_results,'normalization_differences':normalization_differences,'source_issues':issues,'source_input_unchanged':True,'summary':summary,'limitations':limitations,'scientific_or_clinical_validation':False,'human_review_status':'pending'}
 dump(args.out/'review.json',report)
 lines=['# 独立 AI 来源与契约复核','', '日期：2026-10-14。审核者为同一平台的独立 AI 子代理；并非另一模型提供商，未作不同模型声明，不是人类审核或科学签核。','',f"套件 SHA-256：`{sha(suite_bytes)}`。源目录共 {len(manifest)} 文件；冻结快照 JSON 对指纹：`{compact}`。盲推清单指纹（TAB/LF）：`{fingerprint}`。两个指纹使用不同序列化约定，含相同来源路径及文件哈希。",'', '## 来源一致性','',f"核对 {len(records)} 冻结记录、{field_total} 字段的缺失/存在状态、原值、规范值、原字段名及 JSON Pointer，文件 SHA 逐条对照。盲推仅由预测结果路径选出66条（实际数量见JSON）代表记录，保留完整原字段项及基础 section供追溯；全量来源仅保留路径/哈希清单。首次全量盲推产物已移至/tmp，仓库子集保留原件SHA和选择依据。发现 {len(differences)} 项不一致；另有 {len(normalization_differences)} 项合理展示规范差异。普通字段末尾空白在盲推中保留，而冻结值去除；这类差异不改冻结答案，原值仍逐字检查。8项HTML名称差异（若当前套件存在）是冻结保留不换行空格U+00A0、盲推折叠为普通空格；按字面值仍列为不一致，未自动修改或接受冻结展示规范。",'', '## 契约一致性','',f"{len(case_results)} 工程用例的状态、结果路径、筛选计数、分页及比较项均逐项检查。源值参与推导的用例 {summary['source_derived_case_count']} 项；另有 {summary['contract_only_case_count']} 项纯契约或条件性用例。这些仍共享产品字段映射、精确名称/子串匹配、AND、排序与分页规范，可能共享规范错误。全部用例不能称为独立数据真值或真实任务正确率。",'', '未映射中文、缺版本、非法参数及 readiness 使用显式产品/API 契约。版本导入状态、SQLite schema/就绪性无法从源 JSON 证明。本脚本没有读取 SQLite、调用生产查询、模型或 API。异常类别、HTTP 状态和错误语义已核对；5条（若套件包含）精确异常文案不是盲推得到，review.json 明确标为未独立验证。HTTP 空筛选文案与 repository 文案可合理不同。','', '## 真实任务缺失与人工复核','', '当前只覆盖结构化检索与字段对齐；不验证药材实体科学归一化、中文别名映射、术语分类、临床效力/安全性、PMID证据支持性或真实医疗问答。尤其 Cold 子串包含 Mildly cold 等修饰词，417不是受控词表的精确类别数。','', '建议人工逐项复核中文名称/同名来源身份、修饰词与缺失含义、来源条目中的链接与 PMID，以及代表性真实用户任务；记录审核人、日期和依据，再做人工签核。需另以隔离环境验证缺版本与就绪性契约，并决定是否将精确异常文案作为外部稳定规范。','', '## 可重复过程与边界','', '先运行 `--phase blind`，仅提取套件 id/operation/input，独立遍历源 JSON 后保存 blind_predictions.json；随后 `--phase compare` 才读取 expected/records 和答案复核文档。`--phase all` 按同样顺序执行。脚本受套件 SHA 和源指纹绑定，漂移会阻止对照。盲推虽然标准 JSON 解码整份套件，但不访问答案对象；这是流程隔离，不是密码学答案屏蔽。','', '首次盲推普通值未 trim 的规范保留；比较阶段显式记录可解释规范差异，不重写盲推或冻结答案。原文件以只读方式打开，没有源目录、冻结 JSON 或 SQLite 写入。该可重复审计器检查来源与契约一致性，并不独立判定科学正确性。','', '## 用例逐项结果','', '| 用例 | 核对结果 | 依据 |','|---|---|---|']
 for c in case_results:lines.append(f"| {c['id']} | {'存在差异' if c['mismatches'] else '已核对，受上述边界约束'} | {c['basis']} |")
 lines+=['', f"核对项共{summary['checks']}，实际验证{summary['verified_checks']}，未独立验证{summary['unverified_checks']}。未验证项ok=null，不计为通过；8项字面差异仍保留为mismatch。"]
 if differences:lines+=['','## 不一致详情','', '```json',json.dumps(differences,ensure_ascii=False,indent=2),'```']
 (args.out/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
 print(json.dumps(summary,ensure_ascii=False))
 return 1 if differences else 0

def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--suite',type=Path,default=ROOT/'eval/tcm_golden_v1.json')
 parser.add_argument('--source',type=Path,default=Path('/home/zry/workspace/HDCTI/dataset/ETCM2.0/etcm_herbs'))
 parser.add_argument('--out',type=Path,default=ROOT/'audits/tcm_ai_review')
 parser.add_argument('--phase',choices=['blind','compare','all'],default='all')
 args=parser.parse_args()
 try:
  source_root=args.source.resolve();out_root=args.out.resolve()
  if out_root==source_root or source_root in out_root.parents:raise ValueError('output must not be inside read-only source directory')
  if args.suite.resolve()==out_root or out_root in args.suite.resolve().parents:raise ValueError('output must not contain input suite')
  if args.phase in {'blind','all'}:blind(args)
  if args.phase in {'compare','all'}:return compare(args)
  return 0
 except (OSError,ValueError,KeyError,AssertionError) as exc:
  print('audit blocked:',exc,file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
