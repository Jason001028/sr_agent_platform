import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.agent.loop import run_loop
from backend.config import Config
from backend.services.skills import SkillCatalog, prepare_agent
from backend.tools.contract import ToolRegistry
from backend.tests.test_api_platform import PlatformBase


def write_skill(root, name='scene-review', extra=''):
    directory = Path(root) / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'SKILL.md').write_text(
        f'---\nname: {name}\ndescription: Review scenes\n{extra}---\n'
        'BODY_SENTINEL: use search_scenes before selecting a scene.\n', encoding='utf-8')
    return directory


class TestSkills(unittest.TestCase):
    def test_discovery_diagnostics_and_lazy_loading(self):
        with tempfile.TemporaryDirectory() as td:
            skill = write_skill(td)
            (skill / 'notes.txt').write_text('supporting text', encoding='utf-8')
            write_skill(td, 'manual-only', 'disable-model-invocation: true\n')
            bad = Path(td) / 'bad'
            bad.mkdir()
            (bad / 'SKILL.md').write_text('no frontmatter', encoding='utf-8')
            catalog = SkillCatalog([td, td])
            self.assertEqual(len(catalog.skills), 2)
            self.assertTrue(catalog.diagnostics)
            self.assertNotIn('BODY_SENTINEL', catalog.prompt())
            self.assertNotIn('manual-only', catalog.prompt())
            self.assertEqual(catalog.read('scene-review', 'notes.txt')['content'], 'supporting text')
            for resource in ('../bad/SKILL.md', '/etc/passwd', '..\\bad\\SKILL.md'):
                with self.assertRaises(ValueError):
                    catalog.read('scene-review', resource)
            (skill / 'large').write_bytes(b'x' * (256 * 1024 + 1))
            with self.assertRaises(ValueError):
                catalog.read('scene-review', 'large')
            registry, _, prompt = prepare_agent(catalog, ToolRegistry(), 'system', '/skill:manual-only inspect MSS')
            self.assertIn('BODY_SENTINEL', prompt)
            self.assertIn('inspect MSS', prompt)
            self.assertTrue(registry.invoke('read_skill', {'name': 'scene-review'})['ok'])
            self.assertFalse(registry.invoke('read_skill', {'name': 'unknown'})['ok'])

    def test_agent_loads_skill_then_uses_result(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {'SR_SKILL_ROOTS': td}):
            write_skill(td)
            registry = ToolRegistry()
            calls = []

            def chat(messages, tools):
                calls.append(messages)
                self.assertIn('scene-review', messages[0]['content'])
                if len(calls) == 1:
                    self.assertNotIn('BODY_SENTINEL', messages[0]['content'])
                    self.assertIn('read_skill', [t['function']['name'] for t in tools])
                    return {'finish_reason': 'tool_calls', 'content': None, 'tool_calls': [{'id': 'load', 'name': 'read_skill',
                            'arguments': '{"name":"scene-review"}'}]}
                self.assertIn('BODY_SENTINEL', messages[-1]['content'])
                return {'finish_reason': 'stop', 'content': 'loaded', 'tool_calls': []}

            cfg = Config('http://llm/v1', '', 'mock', 16, 0.0, 5.0, True)
            result = run_loop(cfg, 'review this scene', registry=registry, chat=chat)
            self.assertTrue(result['ok'])
            self.assertEqual(result['answer'], 'loaded')
            self.assertEqual(registry.list(), [])


class TestSkillApi(PlatformBase):
    def test_external_skill_reads_and_calls_existing_tools(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {'SR_SKILL_ROOTS': td}):
            write_skill(td)
            c = self.client()
            listing = c.get('/api/skills').json()
            self.assertEqual(listing['skills'][0]['name'], 'scene-review')
            self.assertNotIn('BODY_SENTINEL', str(listing))
            self.assertIn('BODY_SENTINEL', c.get('/api/skills/scene-review').json()['content'])
            self.assertEqual(c.get('/api/skills/nope').status_code, 404)
            self.assertEqual(c.get('/api/skills/scene-review', params={'resource': '../secret'}).status_code, 400)
            response = c.post('/api/skills/scene-review/tools/search_scenes', json={'limit': 1})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()['ok'])
            with patch('backend.api.platform.plugin_svc.enabled_plugin_ids', return_value=set()):
                denied = c.post('/api/skills/scene-review/tools/search_scenes', json={})
                self.assertEqual(denied.status_code, 404)
            self.assertEqual(c.post('/api/skills/scene-review/tools/not_a_tool', json={}).status_code, 404)
            self.assertEqual(c.post('/api/skills/nope/tools/search_scenes', json={}).status_code, 404)
