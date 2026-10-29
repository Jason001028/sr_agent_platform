"""Model-visible contracts, actual defaults and dynamic exposure; no live LLM/SR."""
from copy import deepcopy
import re
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

import pytest

from backend.agent.loop import run_loop
from backend.config import Config
from backend.services.skills import SkillCatalog
from backend.services.store import Store
from backend.services.tool_result_formatter import META, ResultBudget
from backend.services.tool_results import ToolResults
from backend.services.viewer_context import bind_registry, validate_snapshot
from backend.tools import build_builtin_registry
from backend.tools.contract import ok
from backend.tools.run_sr import run_run_sr


def without_descriptions(value):
    if isinstance(value, dict):
        return {k: without_descriptions(v) for k, v in value.items() if k != 'description'}
    if isinstance(value, list):
        return [without_descriptions(v) for v in value]
    return value


def catalog_at(root):
    skill = root / 'scene-review'
    skill.mkdir()
    (skill / 'SKILL.md').write_text(
        '---\nname: scene-review\ndescription: Review scenes\n---\nInspect scene metadata.\n', encoding='utf-8')
    return SkillCatalog([root])


def full_registry(tmp_path):
    registry = build_builtin_registry()
    for tool in catalog_at(tmp_path).tools():
        registry.register(tool)
    return ToolResults().registry(bind_registry(registry, None))


# Execution-relevant schemas are deliberately independent of prose wording.
SCHEMAS = {
    'search_scenes': ({'query': {'type': 'string'}, 'satellite': {'type': 'string'},
        'sensor': {'type': 'string'},
        'date_from': {'type': 'string'}, 'date_to': {'type': 'string'},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 500, 'default': 20},
        'scope': {'type': 'string', 'enum': ['workspace', 'library'], 'default': 'workspace'}}, []),
    'run_sr': ({'lq_path': {'type': 'string'}, 'mask_path': {'type': 'string'},
        'sr_scale': {'type': 'integer', 'minimum': 1, 'default': 2},
        'suffix': {'type': 'string', 'default': ''},
        'gpu': {'type': 'integer', 'minimum': 0, 'default': 0},
        'cloud_limit': {'type': 'integer', 'minimum': 0, 'maximum': 100, 'default': 80},
        'delete_ori': {'type': 'boolean', 'default': False},
        'grid_align': {'type': 'boolean', 'default': True}, 'options_yml': {'type': 'string'}}, ['lq_path']),
    'sr_job_status': ({'job_id': {'type': 'integer'}}, ['job_id']),
    'fix_bad_lines': ({'input_path': {'type': 'string'}, 'output_path': {'type': 'string'},
        'axis': {'type': 'string', 'enum': ['row', 'column'], 'default': 'row'},
        'window': {'type': 'integer', 'minimum': 1, 'default': 5},
        'threshold': {'type': 'number', 'minimum': 0, 'default': 3.0}}, ['input_path']),
    'get_current_image_info': ({}, None),
    'read_tool_result': ({'result_id': {'type': 'string', 'pattern': '^[a-f0-9]{32}$'},
        'path': {'type': 'string', 'maxLength': 512}, 'offset': {'type': 'integer', 'minimum': 0},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20}}, ['result_id']),
    'read_skill': ({'name': {'type': 'string'}}, ['name']),
    'read_skill_resource': ({'name': {'type': 'string'}, 'resource': {'type': 'string'}}, ['name', 'resource']),
}


def test_all_model_contracts_keep_schema_and_handlers(tmp_path):
    registry = full_registry(tmp_path)
    assert {t.name for t in registry.list()} == set(SCHEMAS)
    for tool in registry.list():
        props, required = SCHEMAS[tool.name]
        expected = {'type': 'object', 'properties': props}
        if required is not None:
            expected['required'] = required
        if tool.name in {'get_current_image_info', 'read_tool_result', 'read_skill', 'read_skill_resource'}:
            expected['additionalProperties'] = False
        assert without_descriptions(tool.params_schema) == expected
        assert re.search('[\u4e00-\u9fff]', tool.description)
        assert all(re.search('[\u4e00-\u9fff]', p['description'])
                   for p in tool.params_schema['properties'].values())
        assert tool.openai_function()['function'] == {
            'name': tool.name, 'description': tool.description, 'parameters': tool.params_schema}
    for name in ('search_scenes', 'run_sr', 'sr_job_status', 'fix_bad_lines'):
        assert registry.get(name).run.__module__ == 'backend.tools.' + name
        assert registry.get(name).run.__name__ == 'run_' + name
    assert registry.get('get_current_image_info').run.__name__ == 'current_info'
    assert registry.get('read_tool_result').run.__func__ is ToolResults.read


@pytest.mark.parametrize('skills,allowed', [(False, None), (True, None), (True, set()),
                                          (False, {'search_scenes'})])
def test_loop_sends_actual_descriptions_after_dynamic_binding_and_filtering(tmp_path, monkeypatch, skills, allowed):
    monkeypatch.setenv('SR_SKILL_ROOTS', str(tmp_path))
    catalog = catalog_at(tmp_path) if skills else SkillCatalog([])
    expected = {'get_current_image_info', 'read_tool_result'}
    expected |= {t.name for t in build_builtin_registry().list()} if allowed is None else allowed
    if skills:
        expected |= {'read_skill', 'read_skill_resource'}
    reference = ToolResults().registry(bind_registry(build_builtin_registry(), None))
    for tool in catalog.tools() if skills else []:
        reference.register(tool)
    seen = []

    def chat(messages, tools):
        entries = {t['function']['name']: t for t in tools}
        assert set(entries) == expected
        for name, entry in entries.items():
            assert entry == reference.get(name).openai_function()
        seen.append(entries)
        return {'finish_reason': 'stop', 'content': '已读取工具契约', 'tool_calls': []}

    store = Store(str(tmp_path / 'wire.db')) if allowed is not None else None
    try:
        with patch('backend.agent.loop.plugin_svc.enabled_plugin_ids', return_value=allowed):
            result = run_loop(Config('http://unused', '', 'test', 16, 0.0, 1.0), '查询本图信息',
                              chat=chat, machine_id='test' if allowed is not None else None, store=store)
    finally:
        if store is not None:
            store.close()
    assert result['ok'], result.get('error')
    assert len(seen) == 1


def test_current_info_is_frozen_current_only_and_missing_is_error():
    snapshot = validate_snapshot({'version': 1, 'captured_at': 'send-time',
        'current': {'id': 7, 'name': 'local.tif', 'width': 30},
        'selected_ids': [8], 'displayed': [{'image': {'id': 9, 'name': 'other.tif'}}]})
    registry = bind_registry(build_builtin_registry(), snapshot)
    expected = deepcopy(snapshot['current'])
    snapshot['current']['width'] = 999
    result = registry.invoke('get_current_image_info', {})
    assert result == ok({'image': expected, 'captured_at': 'send-time',
                         'source': 'message_viewer_snapshot', 'attachment_count': 0})
    assert not registry.invoke('get_current_image_info', {'selected_ids': [8]})['ok']
    assert not bind_registry(build_builtin_registry(), None).invoke('get_current_image_info')['ok']
    assert 'current' in registry.get('get_current_image_info').description
    assert 'get_current_image_info' in registry.get('search_scenes').description


def test_run_sr_defaults_are_derived_mask_and_submission_not_finished(tmp_path, monkeypatch):
    scene = tmp_path / 'SCENE'
    scene.mkdir()
    if tmp_path.drive:
        monkeypatch.setenv('SR_DRIVE_MAP', f'{tmp_path.drive}={tmp_path.drive}/')
    monkeypatch.setenv('SR_ALLOWED_ROOTS', tmp_path.as_posix())
    # Capture the real wrapper normalization; do not launch an executor.
    receipt = {'job_id': 42, 'status': 'SUBMITTED', 'idempotent': False}
    with patch('backend.tools.run_sr.svc.submit_run_sr', return_value=receipt) as submit, \
         patch('backend.tools.run_sr.svc.default_suffix', return_value='configured'), \
         patch('backend.tools.run_sr.store_mod.default_store', return_value=Mock()):
        assert run_run_sr(lq_path=scene.as_posix()) == ok(receipt)
    params = submit.call_args.args[0]
    assert params == {'lq_path': scene.as_posix(), 'mask_path': (scene / 'SCENE_mask.tif').as_posix(),
        'sr_scale': 2, 'suffix': 'configured', 'gpu': 0, 'cloud_limit': 80,
        'delete_ori': False, 'grid_align': True, 'options_yml': None, 'repair': True}
    assert not (scene / 'SCENE_mask.tif').exists()  # Tool does not preflight the derived file.
    from backend.services import run_sr as service
    xml = ET.fromstring(service.build_config_xml(params))
    assert xml.findtext('MaskPath') == params['mask_path']
    assert xml.findtext('RepairNOSR') == (scene / 'SCENE_NOSR.tif').as_posix()
    assert xml.findtext('OPT') == service.DEFAULT_OPTIONS_YML
    assert xml.findtext('GPUIDS') == '0'
    description = build_builtin_registry().get('run_sr')
    assert '推导' in description.params_schema['properties']['mask_path']['description']
    assert 'SUBMITTED' in description.description
    assert 'SR_LOCAL_GPU' in description.params_schema['properties']['gpu']['description']


def test_result_reader_default_string_page_escape_and_scope_without_reexecution():
    manager = ToolResults(budget=ResultBudget(max_bytes=2048, max_tokens=2048))
    original = ok({'a/b': {'~name': '文' * 5000}, 'rows': list(range(50))})
    ref = manager.model_view('test', original, 'one')[META]['full_result']['id']
    registry = manager.registry(build_builtin_registry())
    default_page = registry.invoke('read_tool_result', {'result_id': ref, 'path': '/data/rows'})['data']
    assert default_page['value'] == list(range(10))
    assert default_page['next_offset'] == 10 and default_page['has_more']
    page = registry.invoke('read_tool_result', {'result_id': ref, 'path': '/data/a~1b/~0name', 'limit': 2})['data']
    assert page['value'] == '文' * 200 and page['next_offset'] == 200
    assert not registry.invoke('read_tool_result', {'result_id': ref, 'path': '/missing'})['ok']
    assert not ToolResults().read(ref)['ok']
    # Only archived bytes are used, even if all original execution handlers fail.
    with patch.object(registry.get('run_sr'), 'run', side_effect=AssertionError('replay')):
        assert registry.invoke('read_tool_result', {'result_id': ref, 'path': '/data/rows', 'offset': 10})['data']['value'] == list(range(10, 20))
