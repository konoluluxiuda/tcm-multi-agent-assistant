'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const statuses = {running:'可继续',waiting_request:'等待核对原请求清单',waiting_plan:'等待确认任务计划',waiting_sources:'等待确认来源',waiting_targets:'等待选择靶点',complete:'合成流程完成',partial:'证据不足',interrupted:'执行中断',failed:'执行失败',budget_exhausted:'预算已耗尽'};
  const tools = {resolve_sources:'获取来源候选',common_targets:'查共同靶点记录',search_literature:'检索合成研究记录',inspect_study:'核查研究对象与撤稿状态',finish_task:'整理证据和缺口',source_references:'读取来源文献ID（未获取文献）'};
  const scopes = {herb_source_record:'药材本体合成记录',ingredient_only:'仅成分证据，不能替代药材本体',excluded_wrong_object:'排除：对象不符',excluded_retracted:'排除：已撤稿',excluded_wrong_target:'排除：靶点不符'};
  const deliverableLabels = {source_relations:'共同靶点及双方来源记录',evidence_review:'合成研究记录核查',medicinal_part_table:'药用部位并排表'};
  let current = null, busy = false;
  function status(t) {
    if(t.report?.confirmed_scope_complete && t.report.original_request_complete===false)return t.request_draft?'确认范围已完成；确认清单未完成':'确认范围已完成；原请求未完成';
    if(t.data_mode!=='etcm_local' && t.plan?.intent==='source_relations' && t.status==='complete')return t.report?.outcome==='empty'?'合成查询完成：未记录共同标识':'合成来源关系核查完成';
    if(t.data_mode==='etcm_local' && t.report?.result_version===2 && t.status==='complete')return t.report.completion.outcome==='empty'?'本地查询完成：未记录共同标识':'本地关系核查完成';
    return t.data_mode==='etcm_local' && t.report?.local_steps_complete ? '本地关系已核对，研究证据待补' : (statuses[t.status]||t.status);}
  function mode(t) {return t.data_mode==='etcm_local'?'ETCM本地资料 · '+t.names.join('与'):'合成演示 · '+t.profile;}
  function node(tag, text) { const e=document.createElement(tag); if(text!==undefined)e.textContent=text; return e; }
  async function api(path='', options={}) {
    const r=await fetch('/api/research/tasks'+path,{cache:'no-store',...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});
    const data=await r.json(); if(!r.ok) throw new Error(typeof data.detail==='string'?data.detail:'请求无效，请刷新后重试。'); return data;
  }
  async function action(fn) {
    if(busy)return; busy=true; $('feedback').textContent='正在处理…';
    document.querySelectorAll('button').forEach(b=>b.disabled=true);
    try { await fn(); $('feedback').textContent='已更新。'; }
    catch(e) { $('feedback').textContent=e.message; }
    finally { busy=false; document.querySelectorAll('button').forEach(b=>b.disabled=false); }
  }
  function button(text, handler) {const b=node('button',text);b.type='button';b.onclick=()=>action(handler);return b;}
  async function listing() {
    const data=await api(); $('tasks').replaceChildren();
    if(!data.items.length)$('tasks').append(node('li','暂无任务，请先创建。'));
    data.items.forEach(t=>{const li=node('li');li.append(button(`${t.id.slice(0,8)} · ${status(t)} · ${mode(t)}`,async()=>show(await api('/'+t.id))));$('tasks').append(li);});
  }
  async function update(task) {show(task);await listing();}
  function show(t) {
    const local=t.data_mode==='etcm_local';
    const deliverables={...deliverableLabels,...(local?{evidence_review:'文献核查（本任务未执行）'}:{})};
    current=t; $('detail').hidden=false; $('title').textContent=`任务 ${t.id.slice(0,8)} · ${status(t)}`;
    $('summary').textContent=`${t.data_mode==='etcm_local'?'ETCM本地资料 · 只读查询':'虚构资料 · 规则驱动'} · 外部模型调用 ${t.external_model_calls} 次 · 工具动作 ${t.tool_calls}/${t.max_tool_calls} · 状态版本 ${t.revision}`;
    $('question').textContent=t.plan_origin?.question||t.question;
    if(t.plan&&t.pending?.kind!=='request')$('question').textContent+=' 当前范围：'+(t.plan.intent==='source_relations'?'仅来源关系':t.plan.intent==='evidence_review'?'含既有合成研究记录':'待确认')+'；对象：'+(t.plan.herb_one||'待确认')+'、'+(t.plan.herb_two||'待确认')+'。';
    if(t.plan_review_required)$('question').textContent+=' 以下为规划提案，请确认或修改范围和对象后继续。';
    $('request-scope').replaceChildren();
    if(t.request_scope){const r=t.request_scope,c=t.request_coverage;
      $('request-scope').append(node('h3',t.request_draft?'用户核对后的请求清单':'原请求清单'),node('p','对象：'+r.entities.join('、')+'；交付：'+(r.deliverables.map(d=>deliverables[d]).join('、')||'待确认')+'；其他要求：'+((r.other_requirements||[]).join('、')||'无')),
        node('p','当前保存计划未覆盖：'+[...c.uncovered_entities,...c.uncovered_deliverables.map(d=>deliverables[d]),...(c.uncovered_other_requirements||[])].join('、')));
    }
    const form=$('choices');form.replaceChildren();form.hidden=!t.pending;
    if(t.pending?.kind==='request') {
      const draft=t.request_draft;
      form.append(node('p','草稿不是完整识别结果。请对照上方原问句补全对象和要求，取消误识别项；否定和代词不会自动理解。'));
      const group=(title,name,options,selected)=>{form.append(node('strong',title));options.forEach(o=>{const label=node('label');const input=node('input');input.type='checkbox';input.name=name;input.value=o.id;input.checked=selected.includes(o.id);label.append(input,document.createTextNode(o.label));form.append(label);});};
      if(local){const label=node('label','请求对象（每行一项，2至8项；中文名、拼音或来源ID，每项最多100字）：');const input=node('textarea');input.name='request_entities';input.rows=3;input.required=true;input.value=draft.entities.join('\n');label.append(input);form.append(label);}
      else group('请求对象','request_entity',['示例甲草','示例乙草','示例丙草'].map(n=>({id:n,label:n})),draft.entities);
      group('交付要求','request_deliverable',Object.entries(deliverables).map(([id,label])=>({id,label})),draft.deliverables);
      const otherLabel=node('label','未识别或其他要求（每行一项，最多6项、每项120字）：');const other=node('textarea');other.name='other_requirements';other.rows=3;other.value=(draft.other_requirements||[]).join('\n');otherLabel.append(other);form.append(otherLabel);
      const confirmLabel=node('label');const confirm=node('input');confirm.type='checkbox';confirm.name='confirm_complete';confirm.required=true;confirmLabel.append(confirm,document.createTextNode('我已对照原问句核对并修订清单，确认所有对象和要求已列出。'));form.append(confirmLabel);
      const submit=node('button','确认请求清单并生成计划');submit.type='submit';form.append(submit);
      form.onsubmit=e=>{e.preventDefault();action(async()=>{const values=new FormData(form);const entities=local?String(values.get('request_entities')||'').split('\n').map(x=>x.trim()).filter(Boolean):values.getAll('request_entity'),items=values.getAll('request_deliverable'),otherItems=String(values.get('other_requirements')||'').split('\n').map(x=>x.trim()).filter(Boolean);
        if(entities.length<2)throw new Error('请核对并填写至少两个请求对象；不会自动补齐代词指代。');
        if(local&&(entities.length>8||entities.some(n=>n.length>100)))throw new Error('本地请求对象需2至8项，每项最多100字。');
        if(new Set(entities).size!==entities.length)throw new Error('请删除重复对象。');
        if(!items.length&&!otherItems.length)throw new Error('请至少确认一项交付要求，未识别要求可填写在其他要求中。');
        if(otherItems.length>6||otherItems.some(x=>x.length>120))throw new Error('其他要求最多6项，每项最多120字。');
        await update(await api('/'+t.id+'/request',{method:'POST',body:JSON.stringify({revision:t.revision,request_scope:{data_mode:t.data_mode,entities,deliverables:items,other_requirements:otherItems},confirm_complete:values.has('confirm_complete')})}));});};
    }
    else if(t.pending) {
      if(['sources','plan'].includes(t.pending.kind))t.pending.options.forEach(group=>{
        const label=node('label',(group.label||group.name)+'：');const select=node('select');select.name=group.name;select.required=true;
        const blank=node('option',t.pending.kind==='plan'?'请选择计划项':'请选择来源');blank.value='';select.append(blank);
        group.options.forEach(o=>{const option=node('option',o.label+' · '+o.id);option.value=o.id;if(group.suggested===o.id)option.selected=true;select.append(option);});label.append(select);form.append(label);
      });
      else {
        if(t.pending.total!==undefined)form.append(node('p',`来源声明共同标识共 ${t.pending.total} 个，第 ${t.pending.page} 页，本页 ${t.pending.returned_count} 项。`));
        if(t.pending.note)form.append(node('p',t.pending.note));
        if(t.pending.page>1)form.append(button('上一页',async()=>update(await api('/'+t.id+'/targets-page',{method:'POST',body:JSON.stringify({revision:t.revision,page:t.pending.page-1})}))));
        if(t.pending.has_next)form.append(button('下一页',async()=>update(await api('/'+t.id+'/targets-page',{method:'POST',body:JSON.stringify({revision:t.revision,page:t.pending.page+1})}))));
        form.append(node('p','请选择本页1至2个靶点；不会替你自动选择。'));t.pending.options.forEach(o=>{const label=node('label');const input=node('input');input.type='checkbox';input.name='target';input.value=o.id;label.append(input,document.createTextNode(' '+o.id));form.append(label);});}
      const submit=node('button','确认并继续');submit.type='submit';form.append(submit);
      if(t.pending.kind==='plan' && t.request_scope){
        const preview=node('p');const label=node('label');const accept=node('input');accept.type='checkbox';accept.name='accept_reduced_scope';
        label.append(accept,document.createTextNode('我同意仅执行当前选定范围；未覆盖项继续保留为未完成。'));form.insertBefore(preview,submit);form.insertBefore(label,submit);
        const refreshScope=()=>{const values=new FormData(form);const plan={...t.plan,...Object.fromEntries([...values.entries()].filter(([k])=>k!=='accept_reduced_scope'))};
          preview.textContent='当前待确认的执行范围：'+[plan.herb_one,plan.herb_two].filter(Boolean).join('、')+'；'+(deliverables[plan.intent]||'目标待确认')+'。';};
        form.onchange=refreshScope;refreshScope();
      }
      form.onsubmit=e=>{e.preventDefault();action(async()=>{
        const values=new FormData(form);let selection;
        if(['sources','plan'].includes(t.pending.kind))selection=Object.fromEntries([...values.entries()].filter(([k])=>k!=='accept_reduced_scope'));
        else {selection=values.getAll('target');if(selection.length<1||selection.length>2)throw new Error('请选择1至2个靶点。');}
        await update(await api('/'+t.id+'/choose',{method:'POST',body:JSON.stringify({revision:t.revision,selection,accept_reduced_scope:values.has('accept_reduced_scope')})}));
      });};
    }
    $('controls').replaceChildren();
    if(['running','interrupted'].includes(t.status))$('controls').append(button(t.status==='interrupted'?'确认重试中断步骤':'继续任务',async()=>update(await api('/'+t.id+'/resume',{method:'POST',body:JSON.stringify({revision:t.revision,retry_interrupted:t.recovery_required})}))));
    if(t.data_mode==='etcm_local' && t.report?.local_steps_complete && !t.report.reference_leads && t.tool_calls<t.max_tool_calls)
      $('controls').append(button('读取来源文献ID（本地）',async()=>update(await api('/'+t.id+'/references',{method:'POST',body:JSON.stringify({revision:t.revision})}))));
    if(t.report) {const a=node('a','下载证据报告');a.href='/api/research/tasks/'+t.id+'/report';a.download=t.data_mode==='etcm_local'?'etcm-local-task.md':'synthetic-research-task.md';$('controls').append(a);}
    $('controls').append(button('删除本任务',async()=>{await api('/'+t.id+'?revision='+t.revision,{method:'DELETE'});if(current?.id===t.id){current=null;$('detail').hidden=true;}await listing();}));
    $('trace').replaceChildren();t.actions.forEach(e=>{
      const li=node('li');li.append(node('strong',e.kind==='tool'?(tools[e.name]||e.name):e.kind==='user_selection'?'用户确认':'恢复记录'));
      const info=e.kind==='tool'?{参数:e.args,观察:e.observation}:e.selection||e.note;
      const details=node('details');details.append(node('summary','查看记录'),node('pre',JSON.stringify(info,null,2)));li.append(details);$('trace').append(li);
    });
    $('report').replaceChildren();if(t.report){const r=t.report;const area=$('report');area.append(node('h3','证据报告'),node('p',r.notice));
      area.append(node('p','已确认来源：'+r.source_ids.join('、')+'；靶点：'+r.selected_targets.join('、')));
      if(r.request_coverage)area.append(node('p','确认范围完成：'+(r.confirmed_scope_complete?'是':'否')+(t.request_draft?'；确认清单全部完成：':'；原请求全部完成：')+(r.original_request_complete?'是':'否')));
      if(r.data_mode==='etcm_local') {
        area.append(node('p',r.common_target_total===null?'来源尚未确定，共同标识查询未执行。':'全部来源声明共同标识：'+r.common_target_total+' 个；下列只展示本任务选定靶点。'));
        const meta=node('details');meta.append(node('summary','数据版本、文件与快照指纹'),node('pre',JSON.stringify({snapshot:r.snapshot,sources:r.source_records},null,2)));area.append(meta);
        r.relation_evidence.forEach(row=>{const detail=node('details');detail.append(node('summary',row.id+' · 双方原始记录（未外部核验）'),node('pre',JSON.stringify(row,null,2)));area.append(detail);});
      }
      if(!local && t.plan?.intent==='source_relations')r.relation_evidence.forEach(row=>{const detail=node('details');detail.append(node('summary',row.id+' · 双方合成原始记录'),node('pre',JSON.stringify(row,null,2)));area.append(detail);});
      if(r.reference_leads) {
        area.append(node('h3','来源列出文献ID（未读取文献）'),node('p',r.reference_leads.notice));
        r.reference_leads.groups.forEach(g=>{
          const section=node('details');section.append(node('summary',g.source_record_id+' · '+g.total+'条，本次显示'+g.returned_count+'条'+(g.truncated?'（已截断）':'')));
          const list=node('ul');g.candidates.forEach(c=>list.append(node('li','PMID '+c.pmid+(c.valid_identifier?' · 来源声明，身份与靶点关系未核验':' · 标识格式无效'))));
          section.append(list,node('pre',JSON.stringify(g,null,2)));area.append(section);
        });
      }
      const table=node('table');const head=node('tr');['证据编号','对象范围',t.data_mode==='etcm_local'?'来源位置':'合成来源位置'].forEach(x=>head.append(node('th',x)));table.append(head);
      r.study_evidence.forEach(row=>{const tr=node('tr');[row.evidence_id,scopes[row.classification]||row.classification,row.source_locator].forEach(x=>tr.append(node('td',x)));table.append(tr);});area.append(table,node('h3','证据缺口'));
      const gaps=node('ul');(r.gaps.length?r.gaps:[t.data_mode==='etcm_local'?'本次本地查询要求已完成；不代表研究结论或疗效得到验证。':'合成流程要求已完成；不代表真实科研结论成立。']).forEach(g=>gaps.append(node('li',g)));area.append(gaps);
      if(r.limitations?.length){area.append(node('h3','数据与结论边界'));const limits=node('ul');r.limitations.forEach(x=>limits.append(node('li',x)));area.append(limits);}
    }
  }
  $('local-form').onsubmit=e=>{e.preventDefault();action(async()=>update(await api('',{method:'POST',body:JSON.stringify({mode:'etcm_local',names:[$('herb-one').value.trim(),$('herb-two').value.trim()]})})));};
  $('local-question-form').onsubmit=e=>{e.preventDefault();action(async()=>update(await api('',{method:'POST',body:JSON.stringify({mode:'etcm_question',question:$('local-request-question').value})})));};
  $('question-form').onsubmit=e=>{e.preventDefault();action(async()=>update(await api('',{method:'POST',body:JSON.stringify({mode:'synthetic_question',question:$('request-question').value,profile:$('profile').value})})));};
  $('create').onclick=()=>action(async()=>update(await api('',{method:'POST',body:JSON.stringify({profile:$('profile').value})})));
  $('create-plan').onclick=()=>action(async()=>{const intent=$('plan-intent').value;await update(await api('',{method:'POST',body:JSON.stringify({mode:'synthetic_plan',profile:$('profile').value,plan:{intent:intent||null,herb_one:'示例甲草',herb_two:'示例乙草'},request_scope:{entities:['示例甲草','示例乙草',...($('scope-third').checked?['示例丙草']:[])],deliverables:[...(intent?[intent]:[]),...($('scope-parts').checked?['medicinal_part_table']:[])],goal_to_confirm:!intent}})}));});
  $('refresh').onclick=()=>action(async()=>{await listing();if(current)show(await api('/'+current.id));});
  action(listing);
})();
