import concurrent.futures
import threading
import time
import unittest
from types import SimpleNamespace

from agents.runtime import SharedRAG, session_turn
from agents.source_links import source_url


class RuntimeTests(unittest.TestCase):
    def test_session_keys_and_ephemeral_calls(self):
        with session_turn('A') as a, session_turn('B') as b:
            self.assertNotEqual(a, b)
            self.assertEqual(a['configurable']['thread_id'], 'A')
        with session_turn() as a, session_turn() as b:
            self.assertNotEqual(a, b)

    def test_shared_rag_constructed_once_and_serialized(self):
        runtime = SharedRAG()
        state = {'created': 0, 'active': 0, 'max_active': 0, 'closed': 0}
        def query(text, chat_history):
            state['active'] += 1
            state['max_active'] = max(state['max_active'], state['active'])
            time.sleep(0.01)
            state['active'] -= 1
            return text
        def close():
            state['closed'] += 1
        def factory(_):
            state['created'] += 1
            return SimpleNamespace(process_query=query, vector_store=SimpleNamespace(client=SimpleNamespace(close=close)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            answers = list(pool.map(lambda i: runtime.query(factory, None, str(i), ''), range(4)))
        self.assertEqual(answers, ['0','1','2','3'])
        self.assertEqual(state['created'], 1)
        self.assertEqual(state['max_active'], 1)
        runtime.close()
        runtime.close()
        self.assertEqual(state['closed'], 1)

    def test_failed_initialization_can_retry(self):
        runtime = SharedRAG()
        def broken(_): raise ValueError('model unavailable')
        with self.assertRaises(ValueError): runtime.query(broken, None, 'q', '')
        self.assertIsNone(runtime._agent)

    def test_same_session_turns_serialize(self):
        entered = threading.Event()
        done = threading.Event()
        def other():
            entered.set()
            with session_turn('same'):
                done.set()
        with session_turn('same'):
            thread = threading.Thread(target=other)
            thread.start()
            self.assertTrue(entered.wait(1))
            self.assertFalse(done.wait(0.05))
        thread.join(1)
        self.assertTrue(done.is_set())

    def test_relative_sources_and_legacy_urls(self):
        for path in ('./data/raw/paper.pdf', 'data/raw/paper.pdf', 'http://localhost:8000/data/raw/paper.pdf', 'http://localhost:8000/./data/raw/paper.pdf'):
            self.assertEqual(source_url(path), '/data/raw/paper.pdf')
        self.assertEqual(source_url('https://example.org/paper?q=1'), 'https://example.org/paper?q=1')
        self.assertEqual(source_url('./data/raw/a b.pdf'), '/data/raw/a%20b.pdf')
        self.assertIsNone(source_url(None))
