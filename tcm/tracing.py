"""Optional, bounded DataOps observations; never an execution or retry mechanism."""
from contextlib import contextmanager
from functools import lru_cache
import logging
import os
import re

LOG = logging.getLogger(__name__)
_PRIVATE = re.compile(r'password|secret|api.?key|authorization|cookie|headers|reasoning|chain.of.thought', re.I)


def sanitize(value,*,max_depth=8):
    """Limit payload size/depth and strip credentials and hidden reasoning."""
    secrets = [v for k, v in os.environ.items() if _PRIVATE.search(k) and len(v) >= 8]
    budget = [48000]

    def clean(v, depth=0):
        if depth > max_depth or budget[0] <= 0:
            return '[truncated]'
        if isinstance(v, dict):
            return {str(k)[:100]: '[redacted]' if _PRIVATE.search(str(k)) else clean(x, depth+1)
                    for k, x in list(v.items())[:80]}
        if isinstance(v, (list, tuple)):
            return [clean(x, depth+1) for x in v[:60]]
        if isinstance(v, str):
            for secret in secrets:
                v = v.replace(secret, '[redacted]')
            v = re.sub(r'(?i)Bearer\s+\S+|(?:sk|pk)-lf-[A-Za-z0-9_-]+', '[redacted]', v)
            limit = min(4000, budget[0]); budget[0] -= min(len(v), limit)
            return v[:limit] + ('[truncated]' if len(v) > limit else '')
        if v is None or type(v) in (bool, int, float):
            budget[0] -= 16
            return v
        return '[unsupported]'
    return clean(value)


@lru_cache(maxsize=2)
def _create_client(base_url, public_key, secret_key):
    try:
        from langfuse import Langfuse
        return Langfuse(base_url=base_url, public_key=public_key, secret_key=secret_key,
                        timeout=3, flush_interval=1, environment='tcm-local',
                        # Only our explicitly instrumented spans; no whole-state callbacks.
                        should_export_span=lambda s: s.instrumentation_scope.name.startswith('langfuse'))
    except Exception:
        LOG.warning('Langfuse initialization unavailable; execution continues without tracing')
        return None


def client():
    if (os.getenv('ENABLE_LANGFUSE') != '1' or
            os.getenv('LANGFUSE_TRACING_ENABLED', 'false').lower() != 'true'):
        return None
    keys = tuple(os.getenv(k, '') for k in ('LANGFUSE_BASE_URL', 'LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY'))
    return _create_client(*keys) if all(keys) else None


class Observation:
    def __init__(self, span=None):
        self.span = span

    def update(self, **values):
        if self.span is None:
            return
        if os.getenv('TCM_TRACE_CONTENT') != '1':
            values = {k: v for k, v in values.items() if k not in ('input', 'output')}
        try:
            from tcm.dataops.diagnostics import CURRENT
            diagnostic = CURRENT.get()
            if diagnostic and 'metadata' in values:
                values['metadata'] = {**values['metadata'], 'diagnostic_request_id': diagnostic['request_id']}
            self.span.update(**sanitize(values))
        except Exception:
            LOG.warning('Langfuse update unavailable; execution continues')

    def score(self, name, value):
        if self.span is not None:
            try:
                self.span.score(name=name, value=value, data_type='BOOLEAN')
            except Exception:
                LOG.warning('Langfuse score unavailable; local evaluation remains authoritative')


@contextmanager
def observation(name, *, input=None, metadata=None, generation=False, model=None):
    cm = None; span = None
    depth_token = None
    try:
        from tcm.dataops.diagnostics import CURRENT, SPAN_DEPTH
        diagnostic = CURRENT.get()
        depth = SPAN_DEPTH.get()
        depth_token = SPAN_DEPTH.set(depth + 1)
        correlation = {'diagnostic_request_id': diagnostic['request_id']} if diagnostic else {}
        sdk = client()
        if sdk is not None:
            cm = sdk.start_as_current_observation(name=name,
                as_type='generation' if generation else 'span', model=model,
                input=sanitize(input) if os.getenv('TCM_TRACE_CONTENT') == '1' else None,
                metadata=sanitize({**(metadata or {}), **correlation}),
                **({'trace_context': {'trace_id': diagnostic['request_id']}} if diagnostic and depth == 0 else {}))
            span = cm.__enter__()
    except Exception:
        cm = None
        LOG.warning('Langfuse span unavailable; execution continues')
    handle = Observation(span)
    try:
        yield handle
    except BaseException as exc:
        # Do not export exception text, traceback locals or GraphInterrupt payload.
        handle.update(level='WARNING' if type(exc).__name__ == 'GraphInterrupt' else 'ERROR',
                      status_message=type(exc).__name__)
        raise
    finally:
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            except Exception:
                LOG.warning('Langfuse close unavailable; execution continues')
        if depth_token is not None:
            SPAN_DEPTH.reset(depth_token)


def flush():
    sdk = client()
    if sdk is not None:
        try:
            sdk.flush()
        except Exception:
            LOG.warning('Langfuse flush unavailable; local receipts remain authoritative')
