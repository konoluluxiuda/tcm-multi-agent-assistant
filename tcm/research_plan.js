/* Local structural analyses: concise tables, folded provenance; no HTML injection. */
function renderRunEvidence(container,runId,reads){
    const node=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n};
    const labels={target_ids:'靶点标识',rows:'原始记录',items:'候选记录'};
    const group=node('details');group.dataset.evidenceReads='true';group.append(node('summary','完整证据（按需查看）'));container.append(group);
    const downloaded=new Set();
    for(const entry of reads){
        const base='/api/research/runs/'+encodeURIComponent(runId)+'/evidence/'+encodeURIComponent(entry.evidence_id);
        const section=node('div');group.append(section);
        section.append(node('p',(entry.label||labels[entry.field]||entry.field)+(entry.count!==undefined?'（'+entry.count+'项）':'')));
        const load=node('button','读取前20项'),next=node('button','下一页'),previous=node('button','上一页'),body=node('pre');
        for(const button of [load,previous,next]){button.type='button';button.className='btn btn-sm btn-outline-secondary me-1';section.append(button)}
        body.style.whiteSpace='pre-wrap';body.style.overflowWrap='anywhere';section.append(body);previous.hidden=true;next.hidden=true;
        let offset=0,cursor=null,stack=[],busy=false;
        async function read(at,back=false){
            if(busy)return;busy=true;for(const b of [load,next,previous])b.disabled=true;
            try{
                const url=base+'?'+new URLSearchParams({field:entry.field,offset:String(at),limit:'20'});
                const response=await fetch(url,{credentials:'same-origin',cache:'no-store'}),result=await response.json();
                if(!response.ok)throw new Error(result.detail||'证据不可用，请重新核对来源。');
                if(result.status==='error')throw new Error(result.error?.code==='item_exceeds_read_budget'?'单项记录较大，请下载完整证据查看。':'该字段暂时无法读取，请下载完整证据核对。');
                if(back)stack.pop();else if(at!==offset)stack.push(offset);
                offset=at;cursor=result.data.next_offset;body.textContent=JSON.stringify(result.data.items,null,2);
                load.textContent='重新读取本页';next.hidden=cursor===null;previous.hidden=stack.length===0;
            }catch(error){body.textContent=error.message;next.hidden=true;previous.hidden=true}
            finally{busy=false;for(const b of [load,next,previous])b.disabled=false}
        }
        load.addEventListener('click',()=>read(offset));next.addEventListener('click',()=>read(cursor));previous.addEventListener('click',()=>read(stack[stack.length-1],true));
        if(!downloaded.has(entry.evidence_id)){const a=node('a','下载这份完整证据 JSON');a.href=base+'/download';a.setAttribute('download','');section.append(a);downloaded.add(entry.evidence_id)}
    }
}
function attachResearchPlanJob(container,result,send){
    const node=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n};
    const id=result.job_id;container.dataset.planJob=id;
    const progress=node('p','正在连接后台进度…'),controls=node('div');container.append(progress,controls);
    const terminal=new Set(['complete','partial','needs_input','failed','cancelled','interrupted']);
    const labels={queued:'排队中',running:'执行中',complete:'已完成',partial:'部分完成',needs_input:'待补充',failed:'失败',cancelled:'已取消',interrupted:'已中断'};
    let stream=null,busy=false,last=-1;
    async function api(suffix,options={}){
        const response=await fetch('/api/research/jobs/'+encodeURIComponent(id)+suffix,{credentials:'same-origin',cache:'no-store',...options});
        const value=await response.json();if(!response.ok)throw new Error(value.detail||'后台记录暂不可用。');return value;
    }
    function stop(){if(stream)stream.close();stream=null;}
    function button(text,action){const b=node('button',text);b.type='button';b.className='btn btn-sm btn-outline-secondary me-1';
        b.addEventListener('click',async()=>{b.disabled=true;try{await action()}catch(e){progress.textContent=e.message;b.disabled=false}});controls.append(b);return b;}
    async function refresh(){
        if(busy)return;busy=true;
        try{
            const doc=await api('');
            if(!terminal.has(doc.status))return;
            stop();controls.replaceChildren();
            if(doc.result?.display){container.replaceChildren(node('p',doc.result.display.message));renderResearchPlan(container,doc.result.display,send);return;}
            progress.textContent=labels[doc.status]||doc.status;
            if(doc.status==='interrupted'&&doc.recovery==='journal')button('恢复已保存步骤',async()=>{
                await api('/resume',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:doc.revision})});
                container.replaceChildren();attachResearchPlanJob(container,{...result,status:'queued'},send);
            });
            if(doc.status==='failed')progress.textContent+='；请核对来源或执行版本。';
        }catch(e){progress.textContent=e.message;}finally{busy=false;}
    }
    if(!terminal.has(result.status)){
        progress.textContent='后台执行中；关页不会取消任务。';
        button('取消任务',async()=>{await api('/cancel',{method:'POST'});progress.textContent='已请求取消，等待执行器停止。';});
        stream=new EventSource('/api/research/jobs/'+encodeURIComponent(id)+'/events');
        stream.addEventListener('progress',event=>{const data=JSON.parse(event.data);if(data.revision<=last)return;last=data.revision;
            progress.textContent=(labels[data.status]||data.status)+' · 已执行 '+data.tool_calls+' 次工具读取';});
        stream.addEventListener('done',refresh);
        stream.addEventListener('unavailable',()=>{stop();progress.textContent='进度暂不可用，请刷新状态。';});
        stream.onerror=()=>{progress.textContent='进度连接中断，正在重连；任务状态保留。';refresh();};
        window.addEventListener('pagehide',stop,{once:true});
    }
    button('刷新状态',refresh);refresh();
}
function renderResearchPlan(container,result,send){
    container.dataset.researchPlan='true';
    if(result.job_id){attachResearchPlanJob(container,result,send);return;}
    const node=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n};
    const fold=(parent,label)=>{const d=node('details');d.append(node('summary',label));parent.append(d);return d};
    const json=(parent,value)=>{const p=node('pre',JSON.stringify(value,null,2));p.style.whiteSpace='pre-wrap';p.style.overflowWrap='anywhere';parent.append(p)};
    const table=(parent,headers)=>{const wrap=node('div');wrap.style.overflowX='auto';const t=node('table');t.className='table table-sm';const head=node('thead'),r=node('tr');for(const h of headers)r.append(node('th',h));head.append(r);t.append(head);const body=node('tbody');t.append(body);wrap.append(t);parent.append(wrap);return body};
    for(const option of result.options||[]){const row=node('div'),button=node('button','选择 '+option.label);button.type='button';button.className='btn btn-sm btn-outline-primary';button.addEventListener('click',()=>send(option.query));row.append(button);json(fold(row,'来源'),option.source);container.append(row)}
    for(const block of result.blocks||[]){
        const section=node('section');section.dataset.analysisBlock=block.tool;section.append(node('h6',block.title));container.append(section);
        if(block.externalized)section.append(node('p',block.preview_notice));
        if(block.report_kind){
            if(block.report_kind==='pair'){
                const body=table(section,['资料','字段',...(block.herbs||['药材1','药材2'])]);
                for(const item of block.field_rows||[]){const r=node('tr');r.append(node('td',item.source_type),node('td',item.field));
                    for(const side of ['left','right']){const cell=node('td');const values=item[side]||[];
                        cell.append(node('p',values.map(v=>typeof v.value==='string'?v.value:JSON.stringify(v.value)).join('；')||'当前来源缺失'));
                        if(values.length)json(fold(cell,'出处'),values.map(v=>v.evidence));r.append(cell)}body.append(r)}
                if(block.shared_total!==undefined&&block.shared_total!==null){
                    section.append(node('p','共同靶点 '+block.shared_total+' 个；'+(block.all_targets?(block.targets_complete?'全量已读完。':'全量尚未完成，保留已读部分。'):'默认查询首页20项，需全量时请明确要求“全部共同靶点”。')));
                    const d=fold(section,'共同标识与双方原始记录（'+(block.shared_items||[]).length+'项'+(block.externalized||block.target_preview?'预览':'')+'）');
                    for(const item of block.shared_items||[]){const row=fold(d,item.identifier);json(row,item.sources)}
                }
            }else{
                const body=table(section,['字段','内容','出处']);
                for(const item of block.field_rows||[]){const r=node('tr'),cell=node('td');json(fold(cell,'查看'),item.evidence);
                    r.append(node('td',item.field),node('td',typeof item.value==='string'?item.value:JSON.stringify(item.value)),cell);body.append(r)}
            }
            if(block.preview_omitted)section.append(node('p','详细内容请在下方按需读取或下载。'));
            if(block.missing?.length)section.append(node('p','缺项：'+block.missing.join('；')));
        }else if(block.items){
            const metric=block.metric==='common_count'?'共同靶点数':'Jaccard';
            section.append(node('p','按'+metric+'排序，返回'+block.items.length+'个候选（请求前'+block.requested_count+'个）。'));
            const body=table(section,['药材来源','Jaccard','共同靶点','依据']);
            for(const item of block.items){const r=node('tr');r.append(node('td',item.name),node('td',item.jaccard.toFixed(4)),node('td',String(item.shared_count)));const cell=node('td'),d=fold(cell,'查看');d.append(node('p','共同标识：'+item.shared_ids.join('、')));json(d,{source_record_id:item.source_record_id,source:item.source,original_records:item.evidence});r.append(cell);body.append(r)}
            if(!block.items.length)section.append(node('p','当前来源未找到具有共同标识的候选。'));
        }else if(block.rows){
            section.append(node('p',block.target_count+'个不同靶点，'+(block.externalized?'以下为原始记录预览（'+block.rows.length+'条）':'已读取'+block.rows.length+'条原始记录'+(block.has_next?'（尚未读完）':''))+'。'));
            const d=fold(section,'查看原始记录（'+block.rows.length+'条）'),body=table(d,['UniProt','基因符号','原始位置']);
            for(const item of block.rows){const r=node('tr');r.append(node('td',item.uniprot||'未记录'),node('td',item.gene||'未记录'));const c=node('td');json(fold(c,item.json_pointer||'查看'),item.raw_row);r.append(c);body.append(r)}
        }else{if(block.target_count!==undefined)section.append(node('p',block.target_count+'个不同靶点。'));fold(section,'查看靶点标识').append(node('p',(block.target_ids||[]).join('、')||(block.externalized?'完整记录请在下方按需读取或下载。':'当前无可用标识')))}
        json(fold(section,'来源'),block.source);
        if(block.evidence_reads?.length)renderRunEvidence(section,result.run_id,block.evidence_reads);
    }
    if(result.incomplete_requirements?.length){const p=node('p','未完成：'+result.incomplete_requirements.join('；'));p.dataset.analysisIncomplete='true';container.append(p)}
    if(result.execution){const d=fold(container,'执行详情');json(d,{mode:'本地执行',action_calls:result.execution.action_calls,tool_calls:result.execution.tool_calls,stop_reason:result.stop_reason,run_id:result.run_id});d.append(node('p','相似度和候选排序依据来源靶点集合，不代表疗效或治疗推荐。'))}
}
