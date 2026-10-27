import asyncio
import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.services.store import Store
from backend.services.session_titles import (
    fallback_title, generate_title, process_title, title_loop, validate_title,
)


class TitlesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.tmp.name) / 'test.db'))
        self.sid = self.store.create_session({'other': {'keep': True}})
        self.store.append_message(self.sid, {'role': 'user', 'content': '查找GF-2近期影像'})

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def meta(self):
        return self.store.get_session(self.sid)['metadata']

    def test_first_message_atomic_and_once(self):
        self.assertEqual(self.meta()['title_status'], 'pending')
        before = self.meta()
        self.store.append_message(self.sid, {'role': 'user', 'content': 'different'})
        self.assertEqual(self.meta(), before)

    def test_concurrent_claim_one_winner(self):
        def claim(_):
            try:
                return self.store.claim_session_title(self.sid)
            finally:
                self.store.close_thread()
        with ThreadPoolExecutor(4) as pool:
            claims = list(pool.map(claim, range(8)))
        self.assertEqual(sum(bool(c) for c in claims), 1)

    def test_manual_wins_and_metadata_time_pin_preserved(self):
        token = self.store.claim_session_title(self.sid)
        self.store.set_session_pinned(self.sid, True)
        before = self.store.get_session(self.sid)
        self.store.rename_session(self.sid, '手动标题')
        self.assertFalse(self.store.finish_session_title(self.sid, token, '自动标题'))
        after = self.store.get_session(self.sid)
        self.assertEqual(after['updated_at'], before['updated_at'])
        self.assertTrue(after['pinned'])
        self.assertEqual(self.meta()['other'], {'keep': True})
        self.assertEqual(self.meta()['title_source'], 'manual')
        self.assertIsNone(self.store.claim_session_title(self.sid))

    def test_deleted_session_never_recreated(self):
        token = self.store.claim_session_title(self.sid)
        self.store.delete_session(self.sid)
        self.assertFalse(self.store.finish_session_title(self.sid, token, '自动标题'))
        self.assertIsNone(self.store.get_session(self.sid))

    def test_expired_claim_fences_late_response(self):
        old = self.store.claim_session_title(self.sid, ttl=-1)
        new = self.store.claim_session_title(self.sid)
        self.assertNotEqual(old, new)
        self.assertFalse(self.store.finish_session_title(self.sid, old, '过期标题'))
        self.assertTrue(self.store.finish_session_title(self.sid, new, '影像检索'))
        self.assertIsNone(self.store.claim_session_title(self.sid))

    def test_success_no_history_or_budget_pollution(self):
        before = self.store.get_messages(self.sid)
        timestamp = self.store.get_session(self.sid)['updated_at']
        self.assertTrue(process_title(self.store, None, self.sid, lambda cfg, text: 'GF-2影像检索'))
        self.assertEqual(self.meta()['title_source'], 'auto')
        self.assertEqual(self.store.get_messages(self.sid), before)
        self.assertEqual(self.store.get_session(self.sid)['updated_at'], timestamp)
        self.assertFalse(process_title(self.store, None, self.sid, lambda *_: self.fail()))

    def test_failure_is_terminal_fallback(self):
        def fail(*_):
            raise TimeoutError()
        process_title(self.store, None, self.sid, fail)
        self.assertEqual(self.meta()['title_status'], 'failed')
        self.assertEqual(self.meta()['title'], '查找GF-2近期影像')
        self.assertIsNone(self.store.claim_session_title(self.sid))

    def test_image_topic_never_forwarded(self):
        sid = self.store.create_session()
        self.store.append_message(sid, {'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,SECRET'}}]})
        cfg = SimpleNamespace(llm_mock=False)
        process_title(self.store, cfg, sid)
        self.assertEqual(self.store.get_session(sid)['metadata']['title'], '遥感影像分析')

    def test_validation(self):
        for value in ['', 3, 'x'*31, '错误\n标题', '隐藏\u200b标题', '# 标题', '解释。标题', 'only English', '标题😀']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_title(value, auto=True)
        self.assertEqual(validate_title(' GF-2近期影像检索 ', auto=True), 'GF-2近期影像检索')

    def test_request_independent_bounded_and_json(self):
        cfg = SimpleNamespace(llm_mock=False, llm_base_url='http://local/v1', llm_api_key='', llm_model='main')
        with patch('backend.services.session_titles.OpenAI') as ctor:
            client = ctor.return_value.__enter__.return_value
            client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"title":"影像标签分析"}'))])
            self.assertEqual(generate_title(cfg, '影像标签'), '影像标签分析')
            args = client.chat.completions.create.call_args.kwargs
            self.assertFalse(args['stream'])
            self.assertNotIn('tools', args)
            self.assertNotIn('reasoning_effort', args)
            self.assertEqual(args['max_tokens'], 96)
            self.assertEqual(ctor.call_args.kwargs['max_retries'], 0)
            client.chat.completions.create.return_value.choices[0].message.content = None
            with self.assertRaises((TypeError, ValueError)):
                generate_title(cfg, '只有推理没有标题')

    def test_legacy_manual_before_first_message(self):
        sid = self.store.create_session()
        self.store.rename_session(sid, '人工命名')
        self.store.append_message(sid, {'role': 'user', 'content': '问题'})
        self.assertEqual(self.store.get_session(sid)['metadata']['title_source'], 'manual')
        self.assertEqual(len(self.store.pending_session_titles()), 1)


class TitleWorkerTest(unittest.IsolatedAsyncioTestCase):
    async def test_busy_round_does_not_schedule_title(self):
        lock = asyncio.Lock()
        await lock.acquire()
        state = SimpleNamespace(chat_locks={'sid': lock}, store=MagicMock())
        task = asyncio.create_task(title_loop(state))
        await asyncio.sleep(2.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        state.store.pending_session_titles.assert_not_called()
