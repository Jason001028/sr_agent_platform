"""Isolated SQLite/read-only tools: no production Executor, manifest or disk IO."""
import json
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from backend.services.store import Store
from backend.services.viewer_context import bind_registry, validate_snapshot, project_messages
from backend.tools.contract import ToolRegistry


@pytest.fixture
def batch(tmp_path):
    path = tmp_path / 'workspace.db'
    store = Store(path)
    sid = store.create_session()
    rows = []
    for n in range(1, 51):
        rows.append({'item_id':f'i{n}', 'ordinal':n, 'name':f'scene-{n}',
            'status':'PENDING', 'resource_key':f'key-{n}',
            'checkpoint':{'phase':'DETECTING', 'target_image':{'image_key':'pan-key','revision':'r1'},
                'reference_image':{'image_key':'nosr-key'}, 'detection':{'candidates':[{'id':'c1'}]},
                'mask':{'sha256':'mask-hash','version':1}}})
    goal = store.create_batch_goal(title='isolated', request_key='test', content_sha256='hash',
        checkpoint={'workflow':'batch','mode':'demo'}, items=rows, session_id=sid)
    store._db().execute("UPDATE agent_goal_steps SET status='FAILED' WHERE goal_id=? AND item_id='i3'", (goal['goal_id'],))
    store._db().commit()
    yield store, sid, goal['goal_id'], path
    store.close()


def registry(store=None, sid=None, gid=None):
    snapshot = validate_snapshot({'version':1,'current':{'name':'local.tif','image_key':'local-key'},
        'batch_draft':{'total':14,'selected':12,'excluded':2,'version':7},
        'batch_goal':{'goal_id':gid,'item_id':'i3'} if gid else None})
    return bind_registry(ToolRegistry(), snapshot, store=store, session_id=sid)


def test_workspace_pages_detail_and_restart_without_scanning(batch):
    store, sid, gid, path = batch
    with patch('backend.services.scene_search.scan_root', side_effect=AssertionError('scan')), \
         patch('backend.services.batch_manifest.load_manifest', side_effect=AssertionError('manifest')):
        tools = registry(store,sid,gid)
        workspace = tools.invoke('get_repair_workspace')['data']
        assert workspace['draft']['selected'] == 12 and workspace['draft']['excluded'] == 2
        assert workspace['batch']['counts']['total'] == 50
        assert workspace['batch']['counts']['failed'] == 1
        assert workspace['selected_item_id'] == 'i3'
        page = tools.invoke('list_repair_items', {'goal_id':gid,'limit':2})['data']
        assert [i['ordinal'] for i in page['items']] == [1,2]
        page = tools.invoke('list_repair_items', {'goal_id':gid,'limit':2,'cursor':page['next_cursor']})['data']
        assert page['items'][0]['item_id'] == 'i3'
        detail = tools.invoke('get_repair_item', {'goal_id':gid,'item_id':'i3'})['data']
        assert detail['candidate_count'] == 1 and detail['mask_present']
        assert detail['submitted'] is False and detail['review_status'] is None
        assert 'path' not in json.dumps(detail)
        assert not registry(store,'other',gid).invoke('get_repair_item', {'goal_id':gid,'item_id':'i3'})['ok']
        store.close()
        reopened = Store(path)
        try:
            assert registry(reopened,sid,gid).invoke('get_repair_workspace')['data']['batch']['counts']['total'] == 50
        finally:
            reopened.close()


def test_snapshot_frozen_empty_and_budget():
    value = validate_snapshot({'version':1,'current':{'name':'local.tif'},
        'batch_draft':{'total':50,'selected':12,'excluded':2,'version':4,
                       'candidates':[{'name':f'scene-{n}'} for n in range(50)]}})
    tools = bind_registry(ToolRegistry(), value)
    value['current']['name'] = 'changed.tif'
    value['batch_draft']['selected'] = 0
    assert tools.invoke('get_current_image_info')['data']['image']['name'] == 'local.tif'
    assert tools.invoke('get_repair_workspace')['data']['draft']['selected'] == 12
    projected = project_messages([{'role':'user','content':'这批图','viewer_snapshot':value}])
    assert 'scene-49' not in json.dumps(projected)
    empty = bind_registry(ToolRegistry(), None).invoke('get_repair_workspace')
    assert empty['ok'] and empty['data']['image'] is None
    assert not tools.invoke('list_repair_items', {'goal_id':'guess','limit':51})['ok']


def test_known_job_reuses_query_service_and_denies_other_session(batch):
    from backend.tools import build_builtin_registry
    store,sid,gid,_ = batch
    store.put_sr_task('fake',{},session_id=sid,job_id=42)
    tools = bind_registry(build_builtin_registry(),None,store=store,session_id=sid)
    with patch('backend.services.run_sr.query_job_status',return_value={'job_id':42,'state':'RUNNING'}) as executor:
        assert tools.invoke('sr_job_status',{'job_id':42})['data']['state']=='RUNNING'
        executor.assert_called_once()
        denied=bind_registry(build_builtin_registry(),None,store=store,session_id='other')
        assert not denied.invoke('sr_job_status',{'job_id':42})['ok']
        executor.assert_called_once()
        # Legacy Batch receipts have no session; authorize their persisted Item.
        store._db().execute('UPDATE sr_tasks SET session_id=NULL WHERE job_id=42')
        store._db().execute('UPDATE agent_goal_steps SET external_job_id=42 WHERE goal_id=? AND item_id=?',(gid,'i3'))
        store._db().commit()
        assert tools.invoke('sr_job_status',{'job_id':42})['ok']
        assert not denied.invoke('sr_job_status',{'job_id':42})['ok']


def test_layered_verification_does_not_conflate_human_quality(batch):
    store,sid,gid,_ = batch
    verification={'ok':False,'success_scope':'demo_contract','provenance':'mock',
        'layers':{name:{'status':'passed'} for name in ('submit','terminal','contract','artifacts')}}
    verification['layers']['quality']={'status':'unverified','reason':'awaiting review'}
    store._db().execute('UPDATE agent_goal_steps SET verification=?,error=? WHERE goal_id=? AND item_id=?',
        (json.dumps(verification),'bad output D:/private/secrets/PAN.tif',gid,'i3'))
    store._db().commit()
    data=registry(store,sid,gid).invoke('get_repair_item',{'goal_id':gid,'item_id':'i3'})['data']
    assert data['verification']['ok'] is False
    assert data['verification_layers']['contract']['status']=='passed'
    assert data['verification_layers']['quality']['status']=='unverified'
    assert 'D:/private' not in json.dumps(data)


def test_message_recovery_uses_persisted_snapshot_not_current_browser(batch):
    from backend.agent.loop import run_loop
    from backend.config import Config
    store,sid,_,_ = batch
    old=validate_snapshot({'version':1,'current':{'name':'old-local.tif'}})
    store.append_message(sid,{'role':'system','content':'test'})
    store.append_message(sid,{'role':'user','content':'当前影像','viewer_snapshot':old})
    observed=[]
    def chat(messages,tools):
        tool=next((m for m in reversed(messages) if m.get('role')=='tool'),None)
        if tool:
            observed.append(tool['content'])
            return {'finish_reason':'stop','content':'done','tool_calls':[]}
        return {'finish_reason':'tool_calls','content':None,'tool_calls':[
            {'id':'current','name':'get_current_image_info','arguments':'{}'}]}
    result=run_loop(Config('http://unused','','test',32,0,1),None,store=store,session_id=sid,
        resume=True,chat=chat,viewer_snapshot={'version':1,'current':{'name':'new-browser.tif'}})
    assert result['ok']
    assert 'old-local.tif' in observed[0] and 'new-browser.tif' not in observed[0]


def test_legacy_archive_scan_has_a_hard_visit_budget(tmp_path):
    from pathlib import Path
    from backend.services.scene_search import scan_root
    with patch.object(Path,'rglob',return_value=iter([tmp_path]*20001)):
        with pytest.raises(ValueError,match='20000'):
            scan_root(tmp_path)


def test_large_page_preserves_full_result_and_budget(batch):
    from backend.services.tool_results import ToolResults
    from backend.services.tool_result_formatter import size, META
    store,sid,gid,_ = batch
    tools=registry(store,sid,gid)
    manager=ToolResults(store,sid)
    full=tools.invoke('list_repair_items',{'goal_id':gid,'limit':50})
    view=manager.model_view('list_repair_items',full,'workspace-page',
                            tool=tools.get('list_repair_items'))
    assert size(view)<=manager.budget.limit
    assert len(full['data']['items'])==50
    ref=view[META]['full_result']['id']
    assert len(store.read_tool_result(sid,ref)['data']['items'])==50
    assert store.read_tool_result('other-session',ref) is None


@pytest.mark.parametrize('empty',[False,True])
def test_all_workspace_tools_have_zero_search_scan_or_manifest_calls(batch,empty):
    from backend.tools import build_builtin_registry
    store,sid,gid,_=batch
    snapshot=validate_snapshot({'version':1,'current':None if empty else {'name':'local.tif','image_key':'local-id'},
        'workspace':{'version':1,'items':[],'total':0,'local_only':0 if empty else 1},
        'batch_goal':None if empty else {'goal_id':gid}})
    boundaries=['backend.tools.search_scenes._run', 'backend.services.scene_search.search_scenes',
        'backend.services.scene_search.scan_root','backend.tools.search_scenes._workspace_rows',
        'backend.services.batch_manifest.load_manifest','backend.services.batch_manifest.import_manifest',
        'pathlib.Path.rglob']
    # Patches raise as well as count, so a swallowed implementation error cannot
    # accidentally pass: every legitimate query below must still succeed.
    with ExitStack() as stack:
        spies=[stack.enter_context(patch(name,side_effect=AssertionError(name))) for name in boundaries]
        tools=bind_registry(build_builtin_registry(),snapshot,store=store,session_id=sid)
        for name,args in [('get_current_image_info',{}),('get_repair_workspace',{}),
                          ('list_repair_items',{'goal_id':gid,'cursor':2,'limit':1}),
                          ('get_repair_item',{'goal_id':gid,'item_id':'i3'})]:
            result=tools.invoke(name,args)
            assert result['ok'],result
        assert tools.invoke('get_repair_workspace')['data']['context']['workspace_empty'] is empty
        assert all(spy.call_count==0 for spy in spies)


def test_original_ordinal_keeps_exclusions_filters_pages_and_display_reordering(batch):
    store,sid,gid,_=batch
    with store.worker_transaction() as db:
        db.execute("UPDATE agent_goal_steps SET status='SKIPPED',checkpoint=? WHERE goal_id=? AND item_id='i2'",
                   (json.dumps({'source_lines':[],'exclusion_reason':'manual exclusion'}),gid))
        db.execute("UPDATE agent_goal_steps SET status='FAILED',name='first-in-ui' WHERE goal_id=? AND item_id='i4'",(gid,))
    tools=registry(store,sid,gid)
    third=tools.invoke('list_repair_items',{'goal_id':gid,'cursor':2,'limit':1})['data']
    assert third['ordinal_base']==1 and third['ordinals_renumbered'] is False
    assert third['items'][0]['ordinal']==3 and third['items'][0]['item_id']=='i3'
    assert tools.invoke('get_repair_item',{'goal_id':gid,'item_id':third['items'][0]['item_id']})['data']['ordinal']==3
    excluded=tools.invoke('list_repair_items',{'goal_id':gid,'cursor':1,'limit':1})['data']['items'][0]
    assert excluded['item_id']=='i2' and excluded['excluded'] is True
    first=tools.invoke('list_repair_items',{'goal_id':gid,'status':'FAILED','limit':1})['data']
    second=tools.invoke('list_repair_items',{'goal_id':gid,'status':'FAILED','limit':1,'cursor':first['next_cursor']})['data']
    assert [first['items'][0]['ordinal'],second['items'][0]['ordinal']]==[3,4]
    assert tools.invoke('list_repair_items',{'goal_id':gid,'cursor':50,'limit':1})['data']['items']==[]


def test_compact_context_is_frozen_identity_only_and_marks_unknown_vs_empty():
    from backend.services.viewer_context import repair_context_summary
    snapshot=validate_snapshot({'version':1,'current':{'name':'local.tif','image_key':'stable','source_revision':'rev'},
        'batch_draft':{'total':14,'selected':12,'excluded':2,'version':9,'candidates':[{'path':'SECRET-PATH'}]},
        'batch_goal':{'goal_id':'old-goal','item_id':'old-item'},
        'repair_item':{'goal_id':'new-goal','item_id':'foreign-item'},
        'differences':{'status':'FAKE_COMPLETED','regions':[{'bbox':[1,2,3,4]}]}})
    messages=[{'role':'user','content':'third','viewer_snapshot':snapshot}]
    wire=project_messages(messages)
    snapshot['current']['name']='new-browser.tif'
    summary=json.loads(wire[0]['content'][-1]['text'].split('\n',1)[1])['repair_context']
    assert summary['current']['name']=='local.tif'
    assert summary['drafts']['batch_draft']['selected']==12
    assert summary['goal_id']=='old-goal' and summary['item_id']=='old-item'
    assert summary['workspace_empty'] is False
    assert all(text not in json.dumps(wire) for text in ('SECRET-PATH','FAKE_COMPLETED','foreign-item','regions'))
    assert repair_context_summary(None)['workspace_empty'] is None
    assert repair_context_summary({'version':1,'current':None})['workspace_empty'] is True


def test_five_class_evaluation_replay_has_minimal_chain_and_no_real_execution():
    from backend.eval_repair_workspace import evaluate
    with patch('backend.services.run_sr.submit_run_sr',side_effect=AssertionError('production SR')) as sr, \
         patch('backend.services.scene_search.search_scenes',side_effect=AssertionError('production search')) as search, \
         patch('backend.agent.loop._default_chat',side_effect=AssertionError('extra LLM routing')) as llm:
        result=evaluate(live=False,core_only=True)
    enhanced=[row for row in result['rows'] if row['enhanced']]
    assert len(enhanced)==5 and all(row['expected_tool_executed'] for row in enhanced)
    assert sum(row['tool_calls'] for row in enhanced)==6
    assert sum(row['extra_tool_calls'] for row in enhanced)==0
    assert sum(row['irrelevant_searches'] for row in enhanced)==0
    third=next(row for row in enhanced if row['case']=='third')
    assert third['calls']==['list_repair_items','get_repair_item']
    assert third['ordinal_lookup_correct'] is True
    assert all(row['first_tool_correct'] is None for row in enhanced)
    sr.assert_not_called();search.assert_not_called();llm.assert_not_called()
