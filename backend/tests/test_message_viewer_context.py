import json
import pytest
from backend.services.viewer_context import validate_snapshot, bind_registry, project_messages
from backend.tools.contract import ToolRegistry
from backend.agent.loop import run_loop
from backend.config import Config
from backend.services.store import Store


def snap(name='A.tif'):
    return validate_snapshot({'version': 1, 'current': {'id': 1, 'name': name, 'width': 100, 'height': 200},
                              'displayed': [], 'selected_ids': []}, 0)


def test_baseline_missing_state():
    # Reproduce the architectural cause: old user text contains no image identity.
    old = [{'role': 'user', 'content': '请给出本图像信息'}]
    assert 'A.tif' not in json.dumps(old)
    assert not bind_registry(ToolRegistry(), None).invoke('get_current_image_info')['ok']


def test_frozen_binding_and_read_only_schema():
    value = snap()
    registry = bind_registry(ToolRegistry(), value)
    value['current']['name'] = 'B.tif'
    assert registry.invoke('get_current_image_info')['data']['image']['name'] == 'A.tif'
    assert not registry.invoke('get_current_image_info', {'path': '/etc/passwd'})['ok']
    assert bind_registry(ToolRegistry(), snap('B.tif')).invoke('get_current_image_info')['data']['image']['name'] == 'B.tif'


@pytest.mark.parametrize('value', [[], {'version': 2}, {'version': 1, 'current': []},
                                  {'version': 1, 'displayed': {}}, {'version': 1, 'current': {'name': []}}])
def test_bad_snapshot(value):
    with pytest.raises(ValueError):
        validate_snapshot(value)


def test_attachment_count_is_server_owned_and_projection_is_pure():
    value = snap()
    value['attachment_count'] = 99
    assert validate_snapshot(value, 0)['attachment_count'] == 0
    messages = [{'role': 'system', 'content': 'base'}, {'role': 'user', 'content': '本图', 'viewer_snapshot': value}]
    wire = project_messages(messages)
    assert 'viewer_snapshot' not in wire[1]
    assert messages[1]['content'] == '本图'
    assert 'A.tif' in wire[1]['content'][-1]['text']


def test_loop_persistence_switch_and_resume(tmp_path):
    store = Store(tmp_path / 'test.db')
    sid = store.create_session()
    cfg = Config(llm_base_url='http://localhost/v1', llm_api_key='test', llm_model='fake', llm_max_tokens=64, llm_temperature=0, llm_timeout=5)
    for name in ['A.tif', 'B.tif']:
        calls = []
        def chat(messages, tools):
            calls.append(1)
            assert 'viewer_snapshot' not in messages[-1]
            if len(calls) == 1:
                assert name in messages[-1]['content'][-1]['text']
                return {'finish_reason': 'tool_calls', 'content': '', 'tool_calls': [{'id': 'c', 'name': 'get_current_image_info', 'arguments': '{}'}]}
            data = json.loads(messages[-1]['content'])
            assert 'image' in data.get('data', data), data
            assert data.get('data', data)['image']['name'] == name
            assert data.get('data', data)['attachment_count'] == 0
            return {'finish_reason': 'stop', 'content': name, 'tool_calls': []}
        result = run_loop(cfg, '请给出本图像信息', chat=chat, store=store, session_id=sid, resume=True, viewer_snapshot=snap(name))
        assert result['ok'] and result['answer'] == name, result
    users = [m for m in store.get_messages(sid) if m['role'] == 'user']
    assert [m['viewer_snapshot']['current']['name'] for m in users] == ['A.tif', 'B.tif']
    # Crash continuation must bind the latest durable user, with no fresh UI read.
    result = run_loop(cfg, None, chat=lambda messages, tools: {'finish_reason': 'stop', 'content': 'continued', 'tool_calls': []},
                      store=store, session_id=sid, resume=True)
    assert result['ok']
