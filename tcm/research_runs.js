/* Local research uses textContent, never server-provided HTML. */
document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('local-research-form');
    if (!form) return;
    const input = form.querySelector('input'), start = form.querySelector('[type=submit]');
    const cancel = form.querySelector('[data-cancel]'), output = document.getElementById('local-research-output');
    const mode = form.querySelector('[data-research-mode]');
    const reportLabel = form.querySelector('[data-report-question]');
    const reportQuestion = reportLabel.querySelector('input');
    const secondLabel = form.querySelector('[data-second-herb]');
    const secondHerb = secondLabel.querySelector('input');
    mode.addEventListener('change', () => {
        const report = mode.value !== 'disease';
        secondLabel.hidden = mode.value !== 'compare'; secondHerb.required = mode.value === 'compare';
        reportLabel.hidden = !report; reportQuestion.required = report;
        input.maxLength = report ? 80 : 200;
        input.placeholder = report ? '白术' : '疾病英文名或来源 ID';
        start.textContent = report ? '生成资料报告' : '查询靶点';
    });
    let controller, activeJob, stream, pendingSubmission;
    const terminal = new Set(['complete','partial','needs_input','cancelled','failed','interrupted']);
    function node(tag, text) { const n = document.createElement(tag); n.textContent = text; return n; }
    function detail(label, value) {
        const d = node('details', ''); d.append(node('summary', label));
        const pre = node('pre', JSON.stringify(value, null, 2)); pre.style.whiteSpace = 'pre-wrap'; d.append(pre); return d;
    }
    function render(result) {
            output.replaceChildren();
            if (result.kind === 'pair_report') {
                output.append(node('h4', result.herbs.join(' / ') + ' · 联合比较'));
                if (result.status !== 'complete') output.append(node('p', '部分资料未取得，已保留可用结果。'));
                const table = node('table', ''); table.className = 'table table-sm';
                const head = node('tr', ''); ['资料项', ...result.herbs].forEach(x => head.append(node('th', x)));table.append(head);
                for (const row of result.rows || []) {
                    const tr = node('tr', '');tr.append(node('th', row.source_type + ' · ' + row.field));
                    for (const side of ['left','right']) {
                        const cell = node('td', '');
                        if (!row[side].length) cell.append(node('span', '未取得'));
                        for (const value of row[side]) {
                            cell.append(node('p', typeof value.value === 'string' ? value.value : JSON.stringify(value.value)), detail('出处', value.evidence));
                        }
                        if (row[side].length > 1) cell.append(node('small', '存在多处原文，未自动合并。'));
                        tr.append(cell);
                    }
                    table.append(tr);
                }
                output.append(table, node('h5', '共同靶点'));
                const target = result.targets || {};
                if (target.status === 'ok') {
                    output.append(node('p', `${target.total} 个共同 UniProt 标识，当前显示 ${target.items.length} 个。`));
                    const targets = node('table', '');targets.className = 'table table-sm';
                    for (const item of target.items) {
                        const tr = node('tr', '');tr.append(node('td', item.identifier));
                        for (let i=0;i<item.sources.length;i++) {
                            const cell=node('td','');cell.append(detail(result.herbs[i] + '原始记录',item.sources[i]));tr.append(cell);
                        }
                        targets.append(tr);
                    }
                    output.append(targets);
                    if (target.next_offset !== null) {
                        const next = node('button', '在聊天中查询后续靶点');next.type='button';
                        next.addEventListener('click', () => {
                            document.getElementById('message-input').value = '/relations 共同靶点 ' + target.source_ids.join(' | ') + ' 第2页';
                            document.getElementById('chat-form').requestSubmit();
                        });output.append(next);
                    }
                } else {
                    output.append(node('p', {unresolved_sources:'药材来源未确定，未执行共同靶点查询。',same_source:'两种问法指向同一来源，不能作为两味药比较。',version_mismatch:'来源版本不一致，未计算交集。',invalid_evidence:'靶点出处不完整，结果未采用。',timeout:'共同靶点查询超时。'}[target.status] || '共同靶点资料暂不可用。'));
                }
                output.append(node('small','药典提取待审核，ETCM中文名对应未核验；共同靶点不代表相同药效。'));
                const download=node('button','下载报告与出处 JSON');download.type='button';
                download.addEventListener('click', () => {
                    const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));
                    const a=document.createElement('a');a.href=url;a.download='herb-comparison-report.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
                });output.append(download,detail('查询范围与缺项', {scope:result.scope,status:result.status,sources:result.source_reports.map(r=>({herb:r.herb,status:r.status,text_status:r.text?.status,missing_fields:r.text?.missing_fields,structured_status:r.structured?.status}))}));
                return;
            }
            if (result.kind === 'cross_source') {
                output.append(node('h4', result.herb + ' · 资料报告'));
                const labels = {timeout:'查询超时',unavailable:'来源暂不可用',no_match:'没有匹配字段',name_not_resolved:'未识别药材',multiple_entities:'请一次查询一味药',ambiguous_name:'存在多个同名条目',ambiguous_field:'存在炮制范围差异，请核对出处',field_not_resolved:'请补充查询内容'};
                output.append(node('h5', '药典'));
                const text = result.text || {};
                if (!text.hits?.length || text.status !== 'candidates') output.append(node('p', labels[text.status] || '未取得完整药典字段。'));
                for (const hit of text.hits || []) {
                    output.append(node('strong', hit.label), node('p', hit.text), detail('药典出处', hit));
                }
                output.append(node('h5', 'ETCM 基础属性'));
                const structured = result.structured || {};
                if (!structured.rows?.length) output.append(node('p', structured.options?.length ? '存在多个来源，请在药材查询中核对。' : '未取得 ETCM 基础属性。'));
                const table = node('table', ''); table.className = 'table table-sm';
                for (const row of structured.rows || []) {
                    const tr = node('tr', '');
                    tr.append(node('th', row.label), node('td', typeof row.value === 'string' ? row.value : JSON.stringify(row.value)));
                    const td = node('td', ''); td.append(detail('出处', {source:row.source,original_value:row.original_value}));tr.append(td);table.append(tr);
                }
                output.append(table, node('small', '药典提取待审核；ETCM 中文名对应未核验，两侧资料独立展示。'));
                output.append(detail('检索说明', {scope:result.scope,text_status:text.status,structured_status:structured.status,retrieval:text.retrieval,source:structured.source}));
                return;
            }
            if (result.status === 'needs_input') {
                output.append(node('p', result.options.length ? '请选择疾病来源：' : '未找到该疾病。请使用数据库中的英文名或来源 ID。'));
                for (const item of result.options) {
                    const b = node('button', item.name || item.record_id); b.type = 'button';
                    b.addEventListener('click', () => { input.value = item.record_id; form.requestSubmit(); }); output.append(b);
                }
                if (result.options_truncated) output.append(node('p', '仅显示前20个来源，请补充名称缩小范围。'));
                return;
            }
            const run = result.run || {};
            output.append(node('p', result.status === 'complete' ? '查询完成：疾病靶点集合与第一页原始记录。' : '查询未全部完成，请查看执行记录。'));
            for (const evidence of Object.values(run.evidence || {})) {
                if (!['ok', 'empty'].includes(evidence.status) || !evidence.data?.profile) continue;
                const data = evidence.data;
                output.append(node('p', `${data.profile.source.name}：${data.target_ids.length} 个来源靶点标识。`));
                const table = node('table', ''); table.className = 'table table-sm';
                const head = node('tr', ''); ['UniProt', '基因符号', '出处'].forEach(t => head.append(node('th', t))); table.append(head);
                for (const row of data.rows) {
                    const tr = node('tr', ''); tr.append(node('td', row.uniprot || '—'), node('td', row.gene || '—'));
                    const td = node('td', ''); td.append(detail('原始记录', {json_pointer: row.json_pointer, raw_row: row.raw_row})); tr.append(td); table.append(tr);
                }
                output.append(table, detail('来源与完整靶点标识', {source: data.profile.source, target_ids: data.target_ids}));
                if (data.has_next) {
                    const b = node('button', '在聊天中重新查询下一页'); b.type = 'button';
                    b.addEventListener('click', () => { document.getElementById('message-input').value = `/analysis disease ${data.record_id} --page 2`; document.getElementById('chat-form').requestSubmit(); }); output.append(b);
                }
                output.append(node('small', '来源关联不代表疾病机制或治疗效果。'));
            }
            output.append(detail('工具执行记录', {mode: result.mode, status: run.status, reason: run.reason, events: run.events, coverage: run.coverage}));
    }
    const history = document.getElementById('local-research-history');
    const refresh = document.getElementById('local-research-refresh');
    let historyVersion = 0;
    async function api(path, options) {
        const response = await fetch(path, options);
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '研究记录暂不可用。');
        return data;
    }
    async function loadHistory() {
        const version = ++historyVersion;
        try {
            const [jobs, legacy] = await Promise.all([api('/api/research/jobs'), api('/api/research/runs')]);
            const data = {items:[...jobs.items.map(x => ({...x, job:true})), ...legacy.items.map(x => ({...x, job:false}))].sort((a,b) => b.updated-a.updated).slice(0,20)};
            if (version !== historyVersion) return;
            history.replaceChildren();
            if (!data.items.length) history.append(node('p', '暂无研究记录。'));
            const labels = {queued:'排队中',running:'运行中',complete:'已完成',partial:'部分完成',needs_input:'待补充',cancelled:'已取消',failed:'失败',interrupted:'已中断'};
            for (const item of data.items) {
                const row = node('div', `${item.question} · ${labels[item.status] || item.status} `);
                const view = node('button', '回看'); view.type = 'button';
                view.addEventListener('click', async () => {
                    if (controller) return;
                    try {
                        if (item.job && !terminal.has(item.status)) { attach(item.id); return; }
                        const saved = await api((item.job ? '/api/research/jobs/' : '/api/research/runs/') + item.id);
                        if (controller) return;
                        render(saved.result || {status:saved.status,mode:'saved_snapshot',run:saved.snapshot});
                        output.prepend(node('p', '历史快照 · ' + new Date(saved.updated * 1000).toLocaleString() + '；未重新查询资料。'));
                    } catch (e) { output.replaceChildren(node('p', e.message)); }
                });
                const remove = node('button', '删除'); remove.type = 'button'; remove.disabled = !terminal.has(item.status);
                remove.addEventListener('click', async () => {
                    if (controller) return;
                    remove.disabled = true;
                    try { await api((item.job ? '/api/research/jobs/' : '/api/research/runs/') + item.id, {method:'DELETE'}); output.replaceChildren(); await loadHistory(); }
                    catch (e) { history.append(node('p', e.message)); remove.disabled = false; }
                });
                row.append(view, remove); history.append(row);
            }
            const running = jobs.items.find(x => !terminal.has(x.status));
            if (running && !controller) attach(running.id);
        } catch (e) { if (version === historyVersion) history.replaceChildren(node('p', e.message)); }
    }
    refresh.addEventListener('click', loadHistory);
    document.getElementById('local-research-panel').addEventListener('toggle', event => {
        if (event.target.open) loadHistory();
    });
    function release() {
        if (stream) stream.close();
        stream = null; activeJob = null; controller = null;
        start.disabled = false; cancel.disabled = true;
    }
    async function completed(id) {
        if (activeJob !== id) return;
        try {
            const saved = await api('/api/research/jobs/' + id);
            if (activeJob !== id) return;
            if (!terminal.has(saved.status)) return;
            render(saved.result || {status:saved.status,mode:'saved_snapshot',run:saved.snapshot});
            if (!saved.result) output.prepend(node('p', {cancelled:'任务已取消。',interrupted:'任务中断，已保留执行记录；可以重新发起查询。',failed:'任务失败，执行记录已保留。'}[saved.status] || saved.status));
            release(); await loadHistory();
        } catch (e) { output.append(node('p', e.message)); }
    }
    function attach(id) {
        if (stream) stream.close();
        activeJob = id; controller = true; start.disabled = true; cancel.disabled = false;
        output.replaceChildren(node('p', '任务在后台执行。刷新或关闭页面不会取消；可点击取消结束任务。'));
        const progress = node('p', '正在连接进度…'); output.append(progress);
        stream = new EventSource('/api/research/jobs/' + id + '/events');
        let last = -1;
        stream.addEventListener('progress', event => {
            if (activeJob !== id) return;
            const data = JSON.parse(event.data);
            if (data.revision <= last) return;
            last = data.revision;
            const labels = {queued:'排队中',running:'执行中',complete:'已完成',partial:'部分完成',needs_input:'待补充',failed:'失败',cancelled:'已取消',interrupted:'已中断'};
            progress.textContent = `${labels[data.status] || data.status} · 已执行 ${data.tool_calls} 次工具调用`;
            if (data.latest_action?.tool) progress.textContent += ' · ' + data.latest_action.tool;
        });
        stream.addEventListener('done', () => completed(id));
        stream.addEventListener('unavailable', () => {
            if (activeJob !== id) return;
            release(); output.append(node('p','研究记录暂不可用，请刷新历史。'));
        });
        stream.onerror = () => {
            if (activeJob !== id) return;
            progress.textContent = '进度连接暂时中断，正在重连；后台任务继续执行。';
            completed(id);
        };
    }
    cancel.addEventListener('click', async () => {
        if (!activeJob) return;
        cancel.disabled = true;
        try {
            await api('/api/research/jobs/' + activeJob + '/cancel', {method:'POST'});
            output.append(node('p','取消请求已提交，正在等待执行器停止。'));
        } catch (e) { output.append(node('p',e.message)); cancel.disabled = false; }
    });
    form.addEventListener('submit', async event => {
        event.preventDefault(); if (controller || !input.value.trim()) return;
        const question = input.value.trim();
        const report = mode.value !== 'disease';
        const compare = mode.value === 'compare';
        const payload = compare ? {herbs:[question,secondHerb.value.trim()],question:reportQuestion.value.trim()} : report ? {herb:question,question:reportQuestion.value.trim()} : {disease:question};
        const signature = JSON.stringify(payload);
        if (!pendingSubmission || pendingSubmission.signature !== signature) pendingSubmission = {signature,key:crypto.randomUUID()};
        controller = true; start.disabled = true; cancel.disabled = true;
        output.replaceChildren(node('p', '正在提交后台任务…'));
        try {
            const result = await api('/api/research/jobs' + (compare ? '/compare' : report ? '/report' : ''), {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({...payload,request_key:pendingSubmission.key})
            });
            pendingSubmission = null;
            attach(result.run_id); await loadHistory();
        } catch (e) {
            release(); output.replaceChildren(node('p',e.message));
            // Reuse the key on a deliberate retry if the submission response was lost.
        }
    });
});
