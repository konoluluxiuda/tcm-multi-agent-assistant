"""Build an offline HTML replay from saved experiments; no model or service imports."""
import argparse
import hashlib
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_ROOT=ROOT/'var/evidence_assistant_v1'


def load_demo(directory):
    files={
        'first':directory/'real_task_preparation/live_batch_01.json',
        'repair':directory/'real_task_preparation/source_quote_recheck_01.json',
        'boundary_first':directory/'synthetic/boundary_before_01.json',
        'boundary_repair':directory/'synthetic/boundary_after_01.json',
    }
    runs={};fingerprints={}
    for key,path in files.items():
        raw=path.read_bytes();runs[key]={c['id']:c for c in json.loads(raw)['cases']}
        fingerprints[path.name]=hashlib.sha256(raw).hexdigest()
    def snapshot(row):
        result=row['result']
        return {'plan':result.get('plan'),'status':result['status'],'message':result['message'],
                'model_calls':result['model_calls'],'duration_ms':result['duration_ms'],
                'input_tokens':result.get('input_tokens'),'output_tokens':result.get('output_tokens'),
                'tool_statuses':result.get('tool_statuses'),
                'evidence':[{k:e.get(k) for k in ('id','title','text','chunk_id','pmid','truncated')} for e in result.get('evidence') or []]}
    cases=[]
    for identifier,title,note in (
        ('D01','用途查询：引用有效仍会扩写','首轮把并列功效和主治自行配对；修复后保留两份原文列表。'),
        ('D05','字段缺失：保留部分完成','注意字段没有取得；修复后的摘录不添加证候分类，也没有补齐缺失资料。'),
        ('D03','缺少药名：先澄清','首轮正确请求药名；该题没有在新版本重测。'),
        ('D04','未知药名：停止补造','首轮检索未解析药名后停止；不把失败说成现实中不存在。'),
        ('D09','炮制品：保留对象范围','首轮命中独立炮制品条目；该题没有在新版本重测。'),
    ):
        first=runs['first'][identifier];rule=first['rule_baseline']
        cases.append({'id':identifier,'title':title,'note':note,'data_kind':'真实药典开发题',
                      'question':first['question'],'before':snapshot(first),
                      'after':snapshot(runs['repair'][identifier]) if identifier in runs['repair'] else None,
                      'rule':{'status':rule['result']['status'],'route':rule.get('route'),'model_calls':rule['model_calls']}})
    first=runs['boundary_first']['B06'];after=runs['boundary_repair']['B06']
    cases.append({'id':'B06','title':'合成故障：撤稿筛除后的状态','note':'仅为合成文献故障注入；首轮虽排除条目却标ok，修复后partial并给出排除原因。',
                  'data_kind':'合成边界题，不是真实PubMed结果','question':first['question'],
                  'before':snapshot(first),'after':snapshot(after),'rule':None})
    return {'kind':'saved_experiment_replay','replay_model_calls':0,'cases':cases,'input_sha256':fingerprints,
            'limits':['历史回放，不是实时推理；不能用回放速度衡量模型响应时间。',
                      '原两题重测来自同一开发集，不能当作独立准确率。',
                      '药典修复后输出是原文摘录；D05仍缺少注意字段。',
                      '原始资料与回答仅供本地演示；本生成器不联网、不读取.env。']}


def render_page(data):
    encoded=json.dumps(data,ensure_ascii=False).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    return PAGE.replace('__REPLAY_DATA__',encoded)


PAGE='''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TCM研究助手 · 实验回放</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f2f5f5;color:#163136;font:16px/1.65 system-ui,sans-serif}main{max-width:1180px;margin:auto;padding:32px 20px}h1{font-size:29px;margin:0}h2{font-size:20px;margin-top:0}h3{font-size:16px}p{margin:8px 0}.tag{color:#166b60;font-weight:700}.muted{color:#52676b;font-size:14px}.banner{background:#e0eee8;border-left:4px solid #287e67;padding:12px 16px;margin:20px 0}nav{display:flex;gap:8px;flex-wrap:wrap;margin:20px 0}button{font:inherit;cursor:pointer;border:1px solid #a8bdb8;border-radius:7px;background:white;padding:8px 12px;color:#163136}button[aria-pressed=true]{background:#165d51;color:white;border-color:#165d51}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.card{background:white;border:1px solid #d8e2df;border-radius:10px;padding:20px;min-width:0}.status{font:14px ui-monospace,monospace;background:#e8f2ef;padding:3px 8px;border-radius:4px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.7 ui-monospace,monospace;margin:10px 0}.note{background:#fff3da;padding:12px 16px;border-radius:6px}summary{cursor:pointer;color:#17695d}details{margin:12px 0}section+section{border-top:1px solid #d8e2df;padding-top:12px;margin-top:12px}footer{margin:22px 0}.metrics{font-size:14px;color:#52676b}#question{font-size:19px}.empty{color:#657b7b;padding:28px 0}@media(max-width:760px){.grid{grid-template-columns:1fr}main{padding:20px 12px}h1{font-size:24px}}
</style>
<main>
<div class="tag">TCM RESEARCH ASSISTANT / SAVED EXPERIMENTS</div><h1>从引用有效，到来源真正约束输出</h1>
<div class="banner"><strong>历史实测回放 · 本页模型调用为0</strong><p>页面展示已保存结果，非实时推理。记录耗时和token属于当时的模型运行；合成故障与真实资料明确区分。</p></div>
<nav id="cases" aria-label="选择演示用例"></nav>
<div class="card"><span id="kind" class="tag"></span><p id="question"></p><p id="lesson" class="note"></p>
<details><summary>查看首轮规则入口</summary><pre id="rule"></pre></details></div>
<div class="grid" style="margin-top:18px"><article class="card" id="before"><h2>首轮记录</h2><div class="content"></div></article>
<article class="card" id="after"><h2>修复后重测记录</h2><div class="content"></div></article></div>
<footer class="card"><strong>评测边界</strong><div id="limits"></div><details><summary>回放输入指纹</summary><pre id="hashes"></pre></details></footer>
</main>
<script id="replay-data" type="application/json">__REPLAY_DATA__</script>
<script>
'use strict';
const replay=JSON.parse(document.getElementById('replay-data').textContent);
function node(tag,text,cls){const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;return e}
function draw(container,result){container.replaceChildren();if(!result){container.append(node('p','本用例未在修复版本重测，不能补成“修复后通过”。','empty'));return}
container.append(node('span',result.status,'status'));
container.append(node('p','记录调用 '+result.model_calls+' 次 · 记录耗时 '+result.duration_ms+' ms · 输入/输出token '+result.input_tokens+' / '+result.output_tokens+' · 费用未知','metrics'));
const plan=node('details','');plan.append(node('summary','模型的查询计划'),node('pre',JSON.stringify(result.plan,null,2)));container.append(plan);
container.append(node('h3','当时展示的回答'),node('pre',result.message));
const sources=node('details','');sources.append(node('summary','展开当时返回的来源（本地记录）'));
for(const e of result.evidence){const part=node('section','');part.append(node('strong','['+e.id+'] '+e.title),node('pre',e.text),node('p',e.chunk_id||e.pmid||'','muted'));sources.append(part)}container.append(sources);
const tools=node('details','');tools.append(node('summary','工具状态及排除原因'),node('pre',JSON.stringify(result.tool_statuses,null,2)));container.append(tools)}
function choose(identifier){const c=replay.cases.find(x=>x.id===identifier);document.getElementById('kind').textContent=c.data_kind;document.getElementById('question').textContent=c.question;document.getElementById('lesson').textContent=c.note;
document.getElementById('rule').textContent=c.rule?JSON.stringify(c.rule,null,2):'此合成用例未单独记录规则基线。';
draw(document.querySelector('#before .content'),c.before);draw(document.querySelector('#after .content'),c.after);
for(const b of document.querySelectorAll('#cases button'))b.setAttribute('aria-pressed',String(b.dataset.case===identifier))}
for(const c of replay.cases){const b=node('button',c.id+' '+c.title);b.type='button';b.dataset.case=c.id;b.addEventListener('click',()=>choose(c.id));document.getElementById('cases').append(b)}
for(const t of replay.limits)document.getElementById('limits').append(node('p',t,'muted'));
document.getElementById('hashes').textContent=JSON.stringify(replay.input_sha256,null,2);choose(replay.cases[0].id);
</script></html>'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records',type=Path,default=DEFAULT_ROOT)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    data=load_demo(args.records)
    page=render_page(data)
    args.out.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    fd=os.open(args.out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as output:output.write(page)
    print(json.dumps({'status':'created','cases':len(data['cases']),'replay_model_calls':0,'output':str(args.out)},ensure_ascii=False))


if __name__=='__main__':main()
