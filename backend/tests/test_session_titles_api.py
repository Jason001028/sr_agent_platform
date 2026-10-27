import asyncio
import json
import os
from fastapi.testclient import TestClient

from backend.tests.test_api_platform import PlatformBase, sse_events


class TitleApiTests(PlatformBase):
    def _set_env(self):
        super()._set_env()
        os.environ['SR_LLM_MOCK'] = '1'

    def test_rename_api_preserves_pin_time_and_manual_priority(self):
        c = self.client()
        sid = c.post('/api/chat/sessions').json()['session_id']
        before = c.patch(f'/api/chat/sessions/{sid}', json={'pinned': True}).json()
        r = c.patch(f'/api/chat/sessions/{sid}', json={'title': '手动标题'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['updated_at'], before['updated_at'])
        self.assertTrue(r.json()['pinned'])
        self.assertEqual(r.json()['metadata']['title_source'], 'manual')
        c.post(f'/api/chat/sessions/{sid}/messages', json={'content': '查找影像'})
        row = c.get('/api/chat/sessions').json()['sessions'][0]
        self.assertEqual(row['metadata']['title'], '手动标题')
        for value in [None, '', 'x'*31, '换行\n标题']:
            self.assertEqual(c.patch(f'/api/chat/sessions/{sid}', json={'title': value}).status_code, 400)
        self.assertEqual(c.patch('/api/chat/sessions/missing', json={'title': '标题'}).status_code, 404)

    def test_pending_title_sse_and_post_turn_generation(self):
        c = self.client()
        sid = c.post('/api/chat/sessions').json()['session_id']
        events = sse_events(c.post(f'/api/chat/sessions/{sid}/messages', json={'content': '查找GF-2影像'}).text)
        updates = [e for e in events if e['type'] == 'session_update']
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]['session']['metadata']['title_status'], 'pending')
        self.assertTrue(any(e['type'] == 'turn_done' for e in events))
        app = self._apps[-1]
        from backend.services.session_titles import process_title
        process_title(app.state.store, app.state.cfg, sid)
        row = c.get('/api/chat/sessions').json()['sessions'][0]
        self.assertEqual(row['metadata']['title_status'], 'ready')
        self.assertEqual(row['metadata']['title_source'], 'auto')
