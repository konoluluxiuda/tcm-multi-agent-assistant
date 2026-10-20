/* User-created, persistent bookmarks. Notes never become model/tool evidence. */
window.ResearchBookmarks = (() => {
    function mount({renderPreview}) {
        const root = document.getElementById('research-bookmarks');
        if (!root) return null;
        const form = root.querySelector('[data-bookmark-form]');
        const status = root.querySelector('[data-bookmark-status]');
        const focus = root.querySelector('[data-bookmark-focus]');
        const list = root.querySelector('[data-bookmark-list]');
        const preview = root.querySelector('[data-bookmark-preview]');
        const previous = root.querySelector('[data-bookmark-previous]');
        const next = root.querySelector('[data-bookmark-next]');
        let chatBusy = true, working = false, canSave = false, revision = null;
        let offset = 0, nextOffset = null, generation = 0, loaded = false;
        function el(tag, text) {
            const node = document.createElement(tag);
            if (text !== undefined) node.textContent = text;
            return node;
        }
        function controls() {
            root.querySelectorAll('button').forEach(button => button.disabled = chatBusy || working);
            form.querySelector('[type="submit"]').disabled = chatBusy || working || !canSave;
            previous.disabled = chatBusy || working || offset === 0;
            next.disabled = chatBusy || working || nextOffset === null;
        }
        async function api(path = '', options = {}) {
            const response = await fetch('/api/research/bookmarks' + path, {
                credentials: 'include', cache: 'no-store', ...options,
                headers: {'Content-Type': 'application/json', ...options.headers}
            });
            const data = await response.json();
            if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : '收藏请求未完成，请检查输入后重试。');
            return data;
        }
        async function download(path, filename) {
            const response = await fetch('/api/research/bookmarks' + path, {credentials: 'include', cache: 'no-store'});
            if (!response.ok) {
                const data = await response.json();throw Error(data.detail || '导出未完成。');
            }
            const objectUrl = URL.createObjectURL(await response.blob());
            const link = el('a');link.href = objectUrl;link.download = filename;
            document.body.append(link);link.click();link.remove();
            setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
        }
        async function action(task) {
            if (chatBusy || working) return;
            working = true;controls();status.textContent = '正在处理收藏…';
            try { await task(); }
            catch (error) { status.textContent = error.message; }
            finally { working = false;controls(); }
        }
        async function refresh(renderList = false) {
            const serial = ++generation;
            try {
                const data = await api('?offset=' + offset);
                if (serial !== generation || chatBusy) return;
                canSave = data.can_save;revision = data.conversation_revision;
                focus.textContent = data.current_summary ? '可收藏：' + data.current_summary
                    : '完成药典查询或选择ETCM关联来源后，可保存当前查询。';
                if (renderList || !loaded) {
                    list.replaceChildren();
                    for (const item of data.items) list.append(card(item));
                    if (!data.items.length) list.append(el('p', '当前没有收藏。'));
                    nextOffset = data.next_offset;loaded = true;
                    root.querySelector('[data-bookmark-count]').textContent = '共 ' + data.total + ' 项；当前第 ' + (offset / 20 + 1) + ' 页';
                }
                controls();
            } catch (error) { canSave = false;controls();status.textContent = error.message; }
        }
        function card(item) {
            const box = el('article');box.dataset.bookmarkId = item.id;
            box.style.cssText = 'border-top:1px solid #d6e0d5;padding:10px 0';
            box.append(el('strong', item.title));
            const edit = el('details');edit.append(el('summary', '标题与个人笔记'));
            const titleLabel = el('label', '标题');
            const title = el('input');title.value = item.title;title.maxLength = 120;title.required = true;
            title.setAttribute('aria-label', '收藏标题');titleLabel.append(title);
            const noteLabel = el('label', '个人笔记（不作为资料证据）');
            const note = el('textarea');note.value = item.note;note.maxLength = 2000;
            note.setAttribute('aria-label', '收藏个人笔记');note.style.width = '100%';noteLabel.append(note);
            edit.append(titleLabel, noteLabel);box.append(edit);
            const open = el('button', '预览收藏');open.type = 'button';
            open.addEventListener('click', () => action(async () => {
                preview.replaceChildren();
                const data = await api('/' + item.id + '/preview', {method: 'POST'});
                preview.append(el('strong', data.item.title), el('p', '预览来源结果；当前聊天焦点保持不变。'));
                renderPreview(preview, data);
                status.textContent = '已按收藏的来源重新读取本地资料。';
            }));
            const save = el('button', '保存修改');save.type = 'button';
            save.addEventListener('click', () => action(async () => {
                if (!title.value.trim()) throw Error('标题不能为空。');
                await api('/' + item.id, {method: 'PATCH', body: JSON.stringify({
                    revision: item.revision, title: title.value, note: note.value
                })});
                await refresh(true);status.textContent = '标题和个人笔记已保存。';
            }));
            const remove = el('button', '删除收藏');remove.type = 'button';
            remove.addEventListener('click', () => action(async () => {
                await api('/' + item.id + '?revision=' + item.revision, {method: 'DELETE'});
                preview.replaceChildren();offset = 0;await refresh(true);status.textContent = '收藏已删除。';
            }));
            const noteChoice = el('label', '包含已保存的个人笔记');
            const includeNote = el('input');includeNote.type = 'checkbox';
            includeNote.setAttribute('aria-label', '导出时包含已保存的个人笔记');noteChoice.prepend(includeNote);
            const exportNote = el('button', '导出证据笔记');exportNote.type = 'button';
            exportNote.addEventListener('click', () => action(async () => {
                await download('/' + item.id + '/report?revision=' + item.revision + '&include_note=' + includeNote.checked,
                    'tcm-bookmark-evidence.md');
                status.textContent = '证据笔记已下载；仅含本项查询范围，来源与个人笔记分列。';
            }));
            box.append(open, save, remove, noteChoice, exportNote);return box;
        }
        form.addEventListener('submit', event => {
            event.preventDefault();
            action(async () => {
                if (!canSave || revision === null) throw Error('请先完成可收藏的查询。');
                const data = await api('', {method: 'POST', body: JSON.stringify({
                    expected_revision: revision, title: form.elements.title.value, note: form.elements.note.value
                })});
                form.reset();offset = 0;await refresh(true);
                status.textContent = data.created ? '已保存当前查询及来源。' : '此查询已收藏，保留原有个人笔记。';
            });
        });
        root.querySelector('[data-bookmark-refresh]').addEventListener('click', () => action(async () => {
            await refresh(true);status.textContent = '收藏列表已更新。';
        }));
        root.querySelector('[data-bookmark-export]').addEventListener('click', () => action(async () => {
            await download('/export', 'tcm-research-bookmarks-v1.json');
            status.textContent = '备份已下载，包含查询、来源绑定和个人笔记。';
        }));
        root.querySelector('[data-bookmark-import]').addEventListener('click', () => action(async () => {
            const input = root.querySelector('[data-bookmark-import-file]');
            const file = input.files[0];
            if (!file) throw Error('请先选择导出的JSON备份文件。');
            if (file.size > 2 * 1024 * 1024) throw Error('收藏备份不能超过2 MiB。');
            const data = await api('/import', {method: 'POST', body: await file.text()});
            input.value = '';offset = 0;preview.replaceChildren();await refresh(true);
            status.textContent = '已导入 ' + data.imported + ' 项，跳过重复 ' + data.skipped_duplicates +
                ' 项；已有笔记保留，来源将在预览时核验。';
        }));
        previous.addEventListener('click', () => action(async () => { offset = Math.max(0, offset - 20);await refresh(true); }));
        next.addEventListener('click', () => action(async () => { if (nextOffset !== null) { offset = nextOffset;await refresh(true); } }));
        return {setBusy(value) {
            chatBusy = value;generation++;canSave = false;revision = null;
            controls();if (!value) refresh();
        }};
    }
    return {mount};
})();
