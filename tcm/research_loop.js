document.addEventListener('DOMContentLoaded', () => {
    const panel=document.getElementById('synthetic-loop-panel'); if (!panel) return;
    const history=document.getElementById('loop-history'), status=document.getElementById('loop-status');
    let busy=false, sessionVersion=0;
    async function api(path, options={}) {
        const response=await fetch('/api/research/loops'+path,{credentials:'same-origin',...options});
        const data=await response.json();
        if (!response.ok) throw new Error(typeof data.detail==='string' ? data.detail : '请求未通过校验。');
        return data;
    }
    function node(tag,text) {const el=document.createElement(tag);el.textContent=text;return el;}
    async function refresh() {
        const version=sessionVersion, data=await api('');
        if (version!==sessionVersion) return;
        panel.hidden=false;history.replaceChildren();
        for (const doc of data.items) {
            const row=node('div','');
            const label=doc.reason.startsWith('uncertain_') ? '调用结果不确定，已停止，不会重发' :
                doc.phase==='ready' ? '已保存，可继续' : doc.phase==='pending' ? '调用中或等待核对' : doc.status;
            row.append(node('p',label+' · 合成模型 '+doc.model_calls+' 次 · 工具 '+doc.tool_calls+' 次 · 观察 '+doc.evidence_count+' 条'));
            if (doc.phase!=='terminal') {
                const resume=node('button',doc.phase==='pending' ? '核对恢复状态' : '继续执行');resume.type='button';
                resume.onclick=()=>perform(()=>api('/'+doc.id+'/resume',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:doc.revision})}));
                row.append(resume);
            }
            const remove=node('button','删除记录');remove.type='button';
            remove.onclick=()=>perform(()=>api('/'+doc.id,{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:doc.revision})}));
            row.append(remove);history.append(row);
        }
    }
    async function perform(action) {
        if (busy) return;busy=true;status.textContent='处理中…';
        try {await action();await refresh();status.textContent='已更新。';}
        catch (error) {status.textContent=error.message;}
        finally {busy=false;}
    }
    document.getElementById('loop-start').onclick=()=>perform(()=>api('',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pause_after_observation:false})}));
    document.getElementById('loop-pause').onclick=()=>perform(()=>api('',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pause_after_observation:true})}));
    document.getElementById('loop-refresh').onclick=()=>perform(async()=>{});
    // The shared conversation bootstrap creates the cookie. Do not race it by
    // opening a second anonymous session or permanently hiding on an early 401.
    window.addEventListener('tcm-session-ready',()=>{
        sessionVersion++;history.replaceChildren();status.textContent='';refresh().catch(()=>{});
    });
    if (window.tcmSessionReady) refresh().catch(()=>{});
});
