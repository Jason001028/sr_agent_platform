"""P0-3 deterministic formatter, storage, model replay and execution tests."""
import copy
import json
import os
from unittest.mock import Mock, patch

import pytest

from backend.agent.loop import run_loop
from backend.config import Config
from backend.services.store import Store
from backend.services.tool_result_formatter import META, ResultBudget, format_result, size
from backend.services.tool_results import ToolResults
from backend.tools.contract import Tool, ToolRegistry, ok, err

BUDGET = ResultBudget(max_bytes=2048, max_tokens=2048)


def check(view, budget=BUDGET):
    assert size(view) <= budget.limit
    assert json.loads(json.dumps(view, ensure_ascii=False)) == view
    return view


@pytest.mark.parametrize('result', [ok(None), ok({}), ok([]), ok('short'), err('failed'), ok({'state':'UNKNOWN','skip':1})])
def test_short_results_unchanged(result):
    assert format_result('test', result, budget=BUDGET) == result


@pytest.mark.parametrize('data', ['hello\n'*10000, {'records':list(range(10000))},
    list(range(10000)), {'one':'x'*100000}, {'nested':{'a':{'b':{'c':'x'*10000}}}},
    '中文🙂é'*20000], ids=['text','json','array','field','nested','unicode'])
def test_generic_valid_json_unicode_and_no_mutation(data):
    result = ok(data)
    before = copy.deepcopy(result)
    view = check(format_result('generic', result, budget=BUDGET))
    assert result == before
    assert view[META]['truncated']
    assert view[META]['original_bytes'] > size(view)


@pytest.mark.parametrize('state,skip', [('RUNNING',0),('FAILED',0),('UNKNOWN',0),('COMPLETED',1)])
def test_sr_facts_at_end_preserved(state, skip):
    result = ok({'log':'line\n'*100000, 'job_id':123, 'state':state, 'active':state=='RUNNING',
                 'exit_code':90 if state=='FAILED' else 0, 'verdict':{'skip':skip,'reason':'contract failure' if state=='FAILED' else 'policy'},
                 'contract':{'ok':state=='COMPLETED','skip':skip,'reasons':['policy']}})
    view = check(format_result('sr_job_status', result, budget=BUDGET))
    for field in ('job_id','state','active','exit_code','verdict','contract'):
        assert view['data'][field] == result['data'][field]


def test_long_error_is_explicit_critical_overflow_not_success():
    view = check(format_result('run_sr', err('ERROR '*10000), budget=BUDGET))
    assert view['ok'] is False
    assert view[META]['budget_error'] == 'critical_fields_exceed_budget'


def test_long_sr_logs_keep_error_head_and_tail():
    text = 'start\n' + 'noise\n'*20000 + 'WARNING important\nERROR failure\nfinish'
    view = check(format_result('sr_logs', ok({'log':text}), budget=ResultBudget(max_bytes=4000)))
    log = view['data']['log']
    assert log['truncated']
    assert 'ERROR failure' in log['head'] or 'ERROR failure' in log['tail']
    assert 'finish' in log['tail']
    assert log['original_lines'] == len(text.splitlines())


def test_thousands_of_scenes_have_count_metadata_pagination_and_ids():
    rows = [{'id':f'scene-{i}', 'date':'2026-10-27', 'satellite':'GF', 'sensor':'PAN',
             'path':f'/archive/{i}', 'W':40000,'H':40000} for i in range(3000)]
    full = ok({'count':5000,'scanned':5000,'source':'disk','results':rows})
    view = check(format_result('search_scenes', full, budget=BUDGET, arguments={'query':'GF'}))
    data = view['data']
    assert data['count'] == 5000
    assert data['pagination']['outside_result'] == 2000
    assert data['pagination']['omitted'] + len(data['results']) == 3000
    assert data['pagination']['next']['offset'] == len(data['results'])
    assert data['pagination']['has_more']
    assert data['results'] == rows[:len(data['results'])]
    assert len(data['results']) > 0
    assert view[META]['query'] == {'query':'GF'}


def test_scene_item_cap_applies_even_below_byte_limit():
    full = ok({'count':30,'results':[{'id':str(i)} for i in range(30)]})
    view = format_result('search_scenes', full)
    assert len(view['data']['results']) == 12
    assert view[META]['truncated']


def test_deep_fields_and_huge_critical_field():
    value = {'state':'FAILED'}
    for _ in range(35): value = {'nested':value}
    view = check(format_result('test', ok({'nested':value,'log':'x'*10000}), budget=BUDGET))
    assert 'FAILED' in json.dumps(view) or view[META]['budget_error']
    view = check(format_result('test', ok({'reason':'x'*20000}), budget=BUDGET))
    assert view[META]['budget_error']


def test_invalid_budget_rejected_before_execution():
    with pytest.raises(ValueError): ResultBudget(max_tokens=100)
    with pytest.raises(ValueError): ToolRegistry([Tool('t','',{},Mock(),result_max_bytes=30)])


def test_custom_formatter_cannot_remove_critical_fact_or_mutate_full_result():
    full = ok({'log':'x'*20000,'state':'UNKNOWN'})
    def custom(result):
        result['data']['state']='SUCCESS'
        return result
    tool = Tool('t','',{},Mock(),result_formatter=custom)
    view = check(format_result('t',full,budget=BUDGET,tool=tool))
    assert view['data']['state']=='UNKNOWN'
    assert full['data']['state']=='UNKNOWN'


def test_store_scoped_references_reopen_pagination_and_expiry(tmp_path):
    store = Store(str(tmp_path/'results.db'))
    sid, other = store.create_session(), store.create_session()
    manager = ToolResults(store,sid,BUDGET)
    full = ok({'results':[{'id':str(i)} for i in range(1000)], 'secret':'sensitive'})
    view = manager.model_view('search_scenes',full,'execution-1')
    ref = view[META]['full_result']['id']
    assert store.read_tool_result(sid,ref) == full
    assert store.read_tool_result(other,ref) is None
    assert not ToolResults(store,other,BUDGET).read(ref)['ok']
    store.close()
    recovered = ToolResults(Store(str(tmp_path/'results.db')),sid,BUDGET)
    page = recovered.read(ref,'/data/results',10,5)
    assert page['data']['value'] == full['data']['results'][10:15]
    assert page['data']['next_offset'] == 15
    assert page['data']['has_more']
    assert recovered.read(ref,'/data/secret')['data']['value']=='[REDACTED]'
    assert not recovered.read(ref,'/does-not-exist')['ok']
    with recovered.store.worker_transaction() as db:
        db.execute('UPDATE tool_result_refs SET expires_at=0')
    assert not recovered.read(ref)['ok']
    recovered.store.close()


def test_reference_limits_and_ephemeral_lifetime():
    manager = ToolResults(budget=ResultBudget(archive_bytes=1024))
    view=manager.model_view('test',ok('x'*10000),'k')
    assert view[META]['full_result']=={'available':False,'reason':'archive_size_limit'}
    manager=ToolResults(budget=BUDGET)
    view=manager.model_view('test',ok('x'*10000),'k')
    ref=view[META]['full_result']['id']
    assert manager.read(ref)['ok']
    assert not ToolResults(budget=BUDGET).read(ref)['ok']


def test_archive_failure_never_reexecutes():
    store=Mock()
    store.archive_tool_result.side_effect=OSError('secret path')
    view=ToolResults(store,'s',BUDGET).model_view('test',ok('x'*10000),'k')
    assert view[META]['full_result']=={'available':False,'reason':'archive_unavailable'}


def cfg(): return Config('http://mock','test','mock',128,0.0,5)


def call(name='run_sr'):
    return {'finish_reason':'tool_calls','tool_calls':[{'id':'c1','name':name,'arguments':'{}'}]}


def test_loop_resume_has_bounded_view_full_hook_and_no_reexecution(tmp_path, monkeypatch):
    monkeypatch.setenv('SR_TOOL_RESULT_MAX_BYTES','2048')
    monkeypatch.setenv('SR_TOOL_RESULT_MAX_TOKENS','2048')
    store=Store(str(tmp_path/'loop.db'))
    sid=store.create_session()
    full=ok({'job_id':123,'state':'UNKNOWN','log':'x'*100000})
    execute=Mock(return_value=full)
    registry=ToolRegistry([Tool('run_sr','mock',{'type':'object'},execute)])
    hook=Mock(return_value={'outcome':'WAITING_EXTERNAL'})
    result=run_loop(cfg(),'go',store=store,session_id=sid,registry=registry,
                    chat=lambda m,t:call(),result_hook=hook,max_turns=1,durable=True)
    assert result['outcome']=='WAITING_EXTERNAL'
    execute.assert_called_once()
    assert hook.call_args.args[1] is full
    messages=store.get_messages(sid)
    view=json.loads(messages[-1]['content'])
    check(view)
    reference=view[META]['full_result']['id']
    assert store.read_tool_result(sid,reference)==full
    assert len(messages[-1]['content'].encode()) < 3000
    store.close()
    store=Store(str(tmp_path/'loop.db'))
    seen=[]
    def resume(messages,tools):
        seen.extend(messages)
        return {'finish_reason':'stop','content':'needs reconciliation'}
    result=run_loop(cfg(),None,store=store,session_id=sid,resume=True,registry=registry,chat=resume)
    assert result['ok']
    assert json.loads([m for m in seen if m['role']=='tool'][-1]['content'])==view
    execute.assert_called_once()
    assert store.read_tool_result(sid,reference)==full
    store.close()


def test_legacy_history_is_projected_without_new_effect(tmp_path):
    store=Store(str(tmp_path/'legacy.db'))
    sid=store.create_session()
    store.append_message(sid,{'role':'system','content':'test'})
    store.append_message(sid,{'role':'assistant','content':'','tool_calls':[{'id':'old','function':{'name':'test','arguments':'{}'},'type':'function'}]})
    store.append_message(sid,{'role':'tool','tool_call_id':'old','content':json.dumps(ok('x'*100000))})
    def chat(messages, tools):
        assert len(messages[-1]['content'].encode()) <= 4000
        assert json.loads(messages[-1]['content'])[META]['truncated']
        return {'finish_reason':'stop','content':'done'}
    assert run_loop(cfg(),None,store=store,session_id=sid,resume=True,chat=chat)['ok']
    store.close()


def test_custom_short_summary_cannot_hide_failure():
    tool=Tool('t','',{},Mock(),result_formatter=lambda value: ok({'note':'short'}))
    full=err('failed')
    full['data']={'log':'x'*20000,'state':'FAILED'}
    view=check(format_result('t',full,budget=BUDGET,tool=tool))
    assert view['ok'] is False
    assert view['error']=='failed'
    assert view['data']['state']=='FAILED'


def test_reader_is_a_schema_validated_model_tool(tmp_path):
    full=ok({'rows':[{'id':str(i)} for i in range(1000)]})
    execute=Mock(return_value=full)
    registry=ToolRegistry([Tool('search','mock',{},execute)])
    turns=[]
    def chat(messages,tools):
        turns.append(1)
        if len(turns)==1:return call('search')
        if len(turns)==2:
            view=json.loads(messages[-1]['content'])
            ref=view[META]['full_result']['id']
            return {'finish_reason':'tool_calls','tool_calls':[{'id':'read1','name':'read_tool_result',
                'arguments':json.dumps({'result_id':ref,'path':'/data/rows','offset':50,'limit':3})}]}
        view=json.loads(messages[-1]['content'])
        assert view['data']['value']==full['data']['rows'][50:53]
        return {'finish_reason':'stop','content':'done'}
    store=Store(str(tmp_path/'reader.db'))
    assert run_loop(cfg(),'go',store=store,registry=registry,chat=chat)['ok']
    execute.assert_called_once()
    store.close()


def test_fake_sr_durable_restart_keeps_intents_verifier_and_references(monkeypatch):
    import asyncio
    from backend.tests import test_goal_runtime as fixtures
    case=fixtures.RuntimeTests('test_full_three_step_restart_and_independent_acceptance')
    case.setUp()
    try:
        monkeypatch.setenv('SR_TOOL_RESULT_MAX_BYTES','2048')
        monkeypatch.setenv('SR_TOOL_RESULT_MAX_TOKENS','2048')
        for name in ('search_scenes','run_sr'):
            tool=case.state.tool_registry.get(name)
            original=tool.run
            def verbose(*args, _run=original, **kw):
                result=_run(*args,**kw)
                result['data']['log']='long SR log\n'*10000
                return result
            tool.run=verbose
        async def scenario():
            goal=case.goal()
            gid=goal['goal_id']
            await case.tick(2)
            waiting=case.store.get_goal(gid)
            assert waiting['status']=='WAITING_EXTERNAL'
            assert case.submissions==1
            sid=waiting['session_id']
            messages=case.store.get_messages(sid)
            views=[json.loads(m['content']) for m in messages if m['role']=='tool']
            refs=[view[META]['full_result']['id'] for view in views]
            assert all(size(view)<=2048 for view in views)
            intents=case.store.list_tool_intents(gid)
            assert all(len(i['result']['data']['log'])>10000 for i in intents)
            row=case.store._db().execute('SELECT body,intent_id FROM tool_result_refs WHERE ref_id=?',(refs[0],)).fetchone()
            assert row[0] is None and row[1] == intents[0]['intent_id']
            originals=[case.store.read_tool_result(sid,ref) for ref in refs]
            case.store.close()
            case.store=Store(case.root/'db')
            case.state=case.make_state(case.store)
            assert [case.store.read_tool_result(sid,ref) for ref in refs]==originals
            case.finish_sr()
            await case.tick(4)
            assert case.store.get_goal(gid)['status']=='COMPLETED'
            assert case.submissions==1
            assert case.calls.count('search_scenes')==1
            assert case.calls.count('run_sr')==1
        asyncio.run(scenario())
    finally:case.tearDown()


def test_verdict_inside_array_cannot_be_silently_dropped():
    full=ok({'tasks':[{'log':'x'*10000,'state':'RUNNING'}, {'state':'FAILED','reason':'bad'}]})
    view=check(format_result('batch',full,budget=BUDGET))
    assert view[META]['budget_error']=='critical_fields_exceed_budget'
    assert 'Do not infer success' in view[META]['message']


def test_active_goal_pins_expired_reference_and_session_delete_cleans_it(tmp_path):
    store=Store(str(tmp_path/'pin.db'))
    sid=store.create_session()
    goal=store.create_goal(title='active',session_id=sid)
    manager=ToolResults(store,sid,BUDGET)
    full=ok('x'*10000)
    ref=manager.model_view('test',full,'key')[META]['full_result']['id']
    with store.worker_transaction() as db:db.execute('UPDATE tool_result_refs SET expires_at=0')
    assert store.read_tool_result(sid,ref)==full
    assert store.delete_session(sid)
    assert store.read_tool_result(sid,ref) is None
    store.close()


def test_archive_total_quota_is_based_on_original_bytes(tmp_path):
    store=Store(str(tmp_path/'quota.db'))
    sid=store.create_session()
    manager=ToolResults(store,sid,ResultBudget(archive_bytes=30000,archive_total_bytes=30000))
    first=manager.model_view('test',ok('x'*20000),'one')
    second=manager.model_view('test',ok('x'*20000),'two')
    assert first[META]['full_result']['available']
    assert not second[META]['full_result']['available']
    store.close()


@pytest.mark.parametrize('kind', ['array', 'object', 'text'])
def test_budgeted_pagination_consumes_only_complete_visible_items(kind):
    manager = ToolResults(budget=ResultBudget())
    records = [{'id': str(i), 'description': '较长记录🙂' * 80} for i in range(43)]
    value = (records if kind == 'array' else
             {f'key-{i}': row for i, row in enumerate(records)} if kind == 'object' else
             '中文🙂\n' * 2100)
    full = ok({'records': value})
    ref = manager.model_view('test', full, 'paged')[META]['full_result']['id']
    offset, seen, pages = 0, [], 0
    while True:
        page = manager.read(ref, '/data/records', offset, 20)
        assert size(page) <= manager.budget.limit
        data = page['data']
        visible = data['value']
        assert visible == (dict(list(value.items())[offset:offset+len(visible)])
                           if kind == 'object' else value[offset:offset+len(visible)])
        assert data['shown'] == len(visible)
        assert data['next_offset'] == offset + len(visible)
        assert data['has_more'] == (data['next_offset'] < len(value))
        if kind == 'object':
            assert data['keys'] == list(visible)
            seen.extend(visible.items())
        else:
            seen.extend(visible)
        pages += 1
        if not data['has_more']:
            break
        assert data['next_offset'] > offset
        offset = data['next_offset']
        assert pages < 100
    assert (dict(seen) if kind == 'object' else ''.join(seen) if kind == 'text' else seen) == value
    assert pages > 2


def test_single_record_exceeds_page_budget_does_not_advance():
    manager = ToolResults(budget=ResultBudget())
    full = ok({'rows': [{'id': 'large', 'description': 'x' * 20000}, {'id': 'next'}]})
    ref = manager.model_view('test', full, 'large')[META]['full_result']['id']
    page = manager.read(ref, '/data/rows', 0, 20)
    assert size(page) <= manager.budget.limit
    assert page[META]['budget_error'] == 'page_item_exceeds_budget'
    assert page['data']['value'] == []
    assert page['data']['next_offset'] == 0
    assert page['data']['has_more']
    assert manager.read(ref, '/data/rows/0/id')['data']['value'] == 'large'


def _invalid_custom_view(case):
    if case == 'object':
        return {'data': {'bad': object()}}
    if case == 'nan':
        return {'data': float('nan')}
    if case == 'cycle':
        value = {}
        value['self'] = value
        return value
    return {'data': '\ud800'}


@pytest.mark.parametrize('case', ['object', 'nan', 'cycle', 'unicode'])
def test_invalid_custom_return_falls_back_without_interrupting_result_hook(case, tmp_path):
    full = ok({'job_id': 42, 'state': 'UNKNOWN', 'log': 'x' * 20000})
    before = copy.deepcopy(full)
    execute = Mock(return_value=full)
    tool = Tool('run_sr', 'mock', {}, execute,
                result_formatter=lambda result: _invalid_custom_view(case))
    hook = Mock(return_value=None)
    turns = iter([call(), {'finish_reason': 'stop', 'content': 'reconcile UNKNOWN'}])
    seen = []
    def chat(messages, tools):
        if messages[-1]['role'] == 'tool':
            seen.append(json.loads(messages[-1]['content']))
        return next(turns)
    store = Store(str(tmp_path / 'custom.db'))
    try:
        result = run_loop(cfg(), 'go', store=store, registry=ToolRegistry([tool]),
                          chat=chat, result_hook=hook)
        assert result['ok']
        execute.assert_called_once()
        hook.assert_called_once()
        assert hook.call_args.args[1] is full
        assert full == before
        view = seen[0]
        assert size(view) <= ResultBudget().limit
        assert view['data']['job_id'] == 42
        assert view['data']['state'] == 'UNKNOWN'
        assert view[META]['formatter_error'] in ('TypeError', 'ValueError', 'UnicodeEncodeError')
        ref = view[META]['full_result']['id']
        assert store.read_tool_result(result['session_id'], ref) == full
    finally:
        store.close()
