"""Content-free, per-request diagnostics for the chat HTTP boundary."""
import json
import logging
import time
import uuid

from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders

LOGGER = logging.getLogger('uvicorn.error.tcm_requests')
TOOLS = {'assistant', 'herb', 'research', 'pubmed', 'evidence', 'legacy', 'unknown'}
PHASES = {'validation', 'routing', 'execution', 'response'}
ERRORS = {'none', 'internal_error', 'memory_unavailable', 'conversation_conflict', 'session_expired'}


def mark(request, **values):
    """Only fixed categories enter diagnostics; never copy a payload or exception."""
    trace = getattr(request.state, 'chat_diagnostics', None)
    if trace is None:
        return
    for key, allowed in (('tool', TOOLS), ('phase', PHASES), ('error_kind', ERRORS)):
        if key in values and values[key] in allowed:
            trace[key] = values[key]
    if type(values.get('model_calls')) is int and 0 <= values['model_calls'] <= 8:
        trace['model_calls'] = values['model_calls']


class ChatObservabilityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        tracked = scope.get('path') == '/chat' or scope.get('path','').startswith('/api/dataops/')
        if scope['type'] != 'http' or scope['method'] != 'POST' or not tracked:
            return await self.app(scope, receive, send)
        start = time.monotonic()
        trace = {'event': 'chat_request', 'request_id': uuid.uuid4().hex,
                 'tool': 'unknown', 'phase': 'validation', 'error_kind': 'none',
                 'model_calls': None}
        state = scope.setdefault('state', {})
        state['chat_diagnostics'] = trace
        status, elapsed, response_started = 500, None, False

        async def traced_send(message):
            nonlocal status, elapsed, response_started
            if message['type'] == 'http.response.start':
                response_started = True
                status = message['status']
                elapsed = round(max(0, time.monotonic() - start) * 1000, 2)
                headers = MutableHeaders(scope=message)
                headers['X-Request-ID'] = trace['request_id']
                headers['Server-Timing'] = f'chat;dur={elapsed}'
            await send(message)

        try:
            from tcm.dataops.diagnostics import request_context, record
            from tcm.tracing import observation
            with request_context(trace['request_id']), observation('dataops.request', metadata={'http_path':scope['path']}):
                record('http','received',path=scope['path'])
                try:
                    await self.app(scope, receive, traced_send)
                except Exception as exc:
                    record('http','failed',code='unhandled_exception',exception_type=type(exc).__name__)
                    raise
                finally:
                    record('http','finished',http_status=status)
        except Exception:
            # Routing failures can happen before the endpoint's own try block.
            trace['error_kind'] = 'internal_error'
            if response_started:
                raise  # A response already sent cannot be replaced.
            response = JSONResponse(status_code=500, content={
                'detail': '请求处理失败，请保留请求编号以便排查；未自动重试。'})
            await response(scope, receive, traced_send)
        finally:
            trace.update(http_status=status, duration_ms=elapsed,
                         outcome=('internal_error' if trace['error_kind'] == 'internal_error'
                                  else 'unavailable' if status >= 500
                                  else 'rejected' if status >= 400
                                  else state.get('chat_outcome', 'success')))
            LOGGER.info(json.dumps(trace, ensure_ascii=True, separators=(',', ':')))


def install(app):
    app.add_middleware(ChatObservabilityMiddleware)
