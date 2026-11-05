/* One pending text request per tab. Refresh never dispatches a request. */
(() => {
    const storageKey = 'tcm-chat-pending-v1';
    function create() {
        let scope = null;
        function read() {
            const raw = sessionStorage.getItem(storageKey);
            if (!raw) return null;
            try {
                const body = JSON.parse(raw);
                if (typeof body.query === 'string' && body.query.trim() &&
                    /^[a-f0-9]{64}$/.test(body.request_scope) &&
                    typeof body.request_key === 'string' && /^[A-Za-z0-9_-]{8,100}$/.test(body.request_key) &&
                    ['auto', 'evidence'].includes(body.answer_mode)) return body;
            } catch (_) { /* Discard malformed browser data, never dispatch it. */ }
            sessionStorage.removeItem(storageKey);
            return null;
        }
        function clear() { sessionStorage.removeItem(storageKey); }
        function pending() {
            const body = read();
            if (body && body.request_scope !== scope) { clear(); return null; }
            return body;
        }
        function bind(history) {
            if (!/^[a-f0-9]{64}$/.test(history.request_scope || '')) {
                throw Error('无法确认当前会话，请刷新后重试。');
            }
            scope = history.request_scope;
            const body = pending();
            if (body && (history.acknowledged_request_keys || []).includes(body.request_key)) clear();
        }
        function begin(query, answerMode) {
            if (!scope) throw Error('会话尚未恢复，请刷新后重试。');
            const old = pending();
            const body = {
                query, answer_mode: answerMode, request_scope: scope,
                request_key: old?.query === query && old.answer_mode === answerMode
                    ? old.request_key : crypto.randomUUID(),
                conversation_history: []
            };
            // Persist before POST. A storage failure must not send an untracked request.
            try { sessionStorage.setItem(storageKey, JSON.stringify(body)); }
            catch (_) { throw Error('浏览器无法保存待确认请求，本次未发送；请检查浏览器存储设置。'); }
            return body;
        }
        function complete(key) { if (pending()?.request_key === key) clear(); }
        return {bind, begin, pending, complete, clear};
    }
    window.ChatRequests = {create};
})();
