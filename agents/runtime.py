"""In-process request coordination for the single-worker local deployment."""
from contextlib import contextmanager
from threading import RLock
from uuid import uuid4

# Bounded lock table: the same session serializes turns without keeping a lock
# per user forever. Different sessions can occasionally share a stripe.
_session_locks = [RLock() for _ in range(128)]


@contextmanager
def session_turn(session_id=None):
    key = session_id or str(uuid4())
    with _session_locks[hash(key) % len(_session_locks)]:
        yield {'configurable': {'thread_id': key}}


class SharedRAG:
    """Reuse one local Qdrant owner and serialize its retrieval/model access.

    This is deliberately single-process; multiple workers must use a server DB.
    """
    def __init__(self):
        self._lock = RLock()
        self._agent = None

    def query(self, factory, config, query, chat_history):
        with self._lock:
            if self._agent is None:
                self._agent = factory(config)
            return self._agent.process_query(query, chat_history=chat_history)

    def close(self):
        with self._lock:
            if self._agent is not None:
                self._agent.vector_store.client.close()
                self._agent = None
