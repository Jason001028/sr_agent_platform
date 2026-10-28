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


def workspace(*items, **extra):
    return {'version': 1, 'workspace': {
        'version': extra.pop('version', 0),
        'items': [{'scene_id': i, 'name': n} for i, n in items],
        **extra}}


def test_workspace_validation_normalizes_and_bounds():
    value = validate_snapshot(workspace(
        ('a', 'A'), ('a', '重复'), ('', '空 id'), (None, '没有 id'),
        total=6, local_only=2), 0)
    w = value['workspace']
    assert w['items'] == [{'scene_id': 'a', 'name': 'A'}]
    assert w['total'] == 6
    assert w['local_only'] == 2
    assert w['truncated'] is True      # total 6 > 实际留下 1 条


def test_workspace_rejects_bad_container_but_skips_bad_items():
    for bad in ['x', {'version': -1, 'items': []}, {'items': []},
                {'version': True, 'items': []}, {'version': 1, 'items': {}}]:
        with pytest.raises(ValueError):
            validate_snapshot({'version': 1, 'workspace': bad})
    # 单条坏项是不可信 UI 数据，跳过即可，不该把整条消息打成 400
    value = validate_snapshot({'version': 1, 'workspace': {
        'version': 0, 'items': ['nope', {'name': '没有 id'}, {'scene_id': 'ok', 'name': 3}]}}, 0)
    assert value['workspace']['items'] == [{'scene_id': 'ok', 'name': ''}]


def test_workspace_is_a_tool_scope_not_model_observation():
    """工作区几百条 opaque id 不透传给模型；它是工具的执行范围。"""
    value = validate_snapshot({**workspace(('SECRET-ID', 's'))}, 0)
    wire = project_messages([{'role': 'system', 'content': 'base'},
                             {'role': 'user', 'content': 'hi', 'viewer_snapshot': value}])
    assert 'SECRET-ID' not in json.dumps(wire, ensure_ascii=False)
    assert 'workspace' not in wire[1]['content'][-1]['text']


def test_binding_scopes_search_scenes_to_the_workspace(monkeypatch, tmp_path):
    from backend.tools import build_builtin_registry
    monkeypatch.delenv('SR_SCENES_ROOT', raising=False)
    # 指向不存在的库：不去读开发机上真实的隐藏集合
    monkeypatch.setenv('SR_AGENT_DB', str(tmp_path / 'no-such.db'))
    registry = bind_registry(build_builtin_registry(),
                            validate_snapshot(workspace(('bad-id', '坏图')), 0))
    data = registry.invoke('search_scenes')['data']
    assert data['scope'] == 'workspace'
    assert data['scope_reason'] is None
    assert data['count'] == 0
    assert data['unresolved_count'] == 1


def test_binding_leaves_foreign_tools_named_search_scenes_alone():
    """测试/插件注册的同名工具不认识工作区参数，绑定不该把它换成内置那份。"""
    from backend.tools.contract import Tool, ok
    calls = []

    def fake(**params):
        calls.append(params)
        return ok({'source': 'fake', 'count': 0, 'results': []})

    registry = ToolRegistry()
    registry.register(Tool('search_scenes', '假检索', {}, fake))
    bound = bind_registry(registry, snap())
    assert bound.invoke('search_scenes')['ok']
    assert calls == [{}]
