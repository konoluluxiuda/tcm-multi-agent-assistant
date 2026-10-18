/* Three views over one research task. Switching views never executes a tool. */
window.ResearchWorkspaces = (() => {
    function mount({submit}) {
        const root = document.getElementById('research-workspaces');
        if (!root) return null;
        const tabs = [...root.querySelectorAll('[role="tab"]')];
        const status = root.querySelector('[data-workspace-status]');
        const feedback = root.querySelector('[data-workspace-feedback]');
        let busy = true;
        function activate(id, focus = false) {
            const selected = tabs.find(t => t.id === id) || tabs[0];
            for (const tab of tabs) {
                const active = tab === selected;
                tab.setAttribute('aria-selected', String(active));
                tab.tabIndex = active ? 0 : -1;
                document.getElementById(tab.getAttribute('aria-controls')).hidden = !active;
            }
            if (focus) selected.focus();
        }
        for (const [index, tab] of tabs.entries()) {
            tab.addEventListener('click', () => activate(tab.id));
            tab.addEventListener('keydown', event => {
                const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length
                    : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length
                    : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null;
                if (next !== null) { event.preventDefault(); activate(tabs[next].id, true); }
            });
        }
        activate(tabs[0].id);
        function send(query) {
            if (busy) return;
            feedback.textContent = '';
            if (submit(query)) feedback.textContent = '正在查询，结果将显示在下方共用任务记录中。';
        }
        root.querySelectorAll('[data-workspace-query]').forEach(button => {
            button.addEventListener('click', () => send(button.dataset.workspaceQuery));
        });
        const herbForm = root.querySelector('[data-workspace-herb-form]');
        const source = herbForm.elements.source;
        const field = herbForm.elements.field;
        function syncSource() {
            field.disabled = busy || source.value === 'herb';
            root.querySelector('[data-herb-name-label]').textContent = source.value === 'herb'
                ? 'ETCM拼音、拉丁名、英文名或来源ID' : '药典中文药名';
        }
        source.addEventListener('change', syncSource);
        herbForm.addEventListener('submit', event => {
            event.preventDefault();
            if (busy || !herbForm.reportValidity()) return;
            const name = herbForm.elements.herb_name.value.trim();
            if (!name) return;
            if (name.startsWith('/')) { feedback.textContent = '此处填写药名或来源ID，工具指令请在下方聊天框输入。'; return; }
            send(source.value === 'herb' ? '/herb ' + name : '/research ' + name + '的' + field.value);
        });
        const literatureForm = root.querySelector('[data-workspace-literature-form]');
        literatureForm.addEventListener('submit', event => {
            event.preventDefault();
            if (busy || !literatureForm.reportValidity() || literatureForm.closest('fieldset')?.disabled) return;
            const term = literatureForm.elements.term.value.trim();
            if (term) send('/pubmed ' + term);
        });
        function focusFrom(data) {
            if (data.research) {
                status.textContent = data.research.focus_name
                    ? '当前药典焦点：' + data.research.focus_name
                    : '当前没有确认的药典对象，请核对候选或明确药名。';
            } else if (data.tcm) {
                status.textContent = data.tcm.status === 'ok' ? '当前焦点：ETCM来源记录。尚未映射到药典条目。'
                    : '当前ETCM对象尚未确认，请核对来源候选。';
            } else if (data.pubmed) {
                status.textContent = data.pubmed.tool_result
                    ? '当前焦点：文献批次。与药典对象的对应关系尚未核验。'
                    : '当前没有可用文献批次。';
            }
        }
        return {
            setBusy(value) {
                busy = value;
                root.querySelectorAll('[data-workspace-control]').forEach(el => {
                    el.disabled = value || Boolean(el.closest('fieldset[disabled]'));
                });
                syncSource();
            },
            restore(history) { focusFrom(history); },
            observe(data) {
                focusFrom(data);
                feedback.textContent = '已返回查询结果，请查看下方任务记录与证据卡。';
                if (data.assistant?.topic === 'overview') {
                    const box = root.querySelector('[data-overview-result]');
                    box.replaceChildren();
                    const body = document.createElement('p');body.textContent = data.response;body.style.whiteSpace = 'pre-wrap';box.append(body);
                    const values = data.assistant.herbs?.raw_efficacy_values || [];
                    if (values.length) {
                        const list = document.createElement('ul');
                        list.setAttribute('aria-label', '概览中的原始功效分类');
                        for (const row of values) {
                            const item = document.createElement('li');item.textContent = row.value + '：' + row.record_count + '条记录';list.append(item);
                        }
                        box.append(list);
                    }
                }
            },
            error() { feedback.textContent = '查询未完成，请查看任务记录中的提示；输入内容已保留。'; },
            clear() {
                status.textContent = '当前没有选择资料对象。';feedback.textContent = '';
                herbForm.reset();literatureForm.reset();syncSource();
                root.querySelector('[data-overview-result]').textContent = '点击“更新数据概览”读取当前数量和分类。';
            }
        };
    }
    return {mount};
})();
