import pytest
from backend.services.spatial_annotations import geometry, validate_input, bound_tool
from backend.services.viewer_context import bind_registry
from backend.tools.contract import ToolRegistry

@pytest.fixture
def mapping():
 return dict(input_ref='actual',image_ref=dict(image_key='file-version',source_width=12000,source_height=8000),width=1000,height=500,crop=[2000,1000,4000,2000])

def test_mapping(mapping):
 assert validate_input(mapping,(1000,500))==mapping
 with pytest.raises(ValueError):validate_input(mapping,(500,1000))

@pytest.mark.parametrize('coords', [[float('nan'),2],[float('inf'),2],[-1,1],[10,1],[True,1]])
def test_bad_point(coords):
 with pytest.raises(ValueError):geometry('point',coords,10,10)

@pytest.mark.parametrize('kind,coords',[('bbox',[3,1,2,4]),('polygon',[[0,0],[2,2],[4,4]]),('polygon',[[0,0],[4,4],[0,4],[4,0]])])
def test_bad_regions(kind,coords):
 with pytest.raises(ValueError):geometry(kind,coords,10,10)

def test_result_ids_mapping_and_rejection(mapping):
 tool=bound_tool([mapping]);args=dict(input_ref='actual',annotations=[dict(type='bbox',coordinates=[100,100,200,200],title='<b>candidate</b>',summary='unverified')])
 result=tool.run(**args);a=result['data']['spatial_annotations'][0]
 assert a['geometry']['coordinates']==[2400,1400,2800,1800]
 assert a['verification']=='unverified'
 assert tool.run(**args)==result
 assert not tool.run(**dict(args,input_ref='missing'))['ok']
 assert not bound_tool([]).run(**args)['ok']

def test_opt_in_and_attachment_gate(mapping,monkeypatch):
 monkeypatch.delenv('SR_SPATIAL_GROUNDING',raising=False)
 assert bind_registry(ToolRegistry(),{'visual_inputs':[mapping]}).get('present_spatial_annotations') is not None
 monkeypatch.setenv('SR_SPATIAL_GROUNDING','0')
 assert bind_registry(ToolRegistry(),{'visual_inputs':[mapping]}).get('present_spatial_annotations') is None
 monkeypatch.setenv('SR_SPATIAL_GROUNDING','1')
 assert bind_registry(ToolRegistry(),{}).get('present_spatial_annotations') is None
 assert bind_registry(ToolRegistry(),{'visual_inputs':[mapping]}).get('present_spatial_annotations') is not None


def test_history_preserves_complete_spatial_result(tmp_path,mapping,monkeypatch):
 import json
 from backend.agent.loop import run_loop
 from backend.config import Config
 from backend.services.store import Store
 from backend.api.platform import _project_message
 monkeypatch.setenv('SR_SPATIAL_GROUNDING','1')
 store=Store(tmp_path/'spatial.db');sid=store.create_session();calls=[];events=[]
 def chat(messages,tools):
  calls.append(1)
  if len(calls)==1:
   assert any(t['function']['name']=='present_spatial_annotations' for t in tools)
   return dict(finish_reason='tool_calls',content='',tool_calls=[dict(id='spatial-call',name='present_spatial_annotations',arguments=json.dumps(dict(input_ref='actual',annotations=[dict(type='point',coordinates=[100,100],title='candidate',summary='x'*500) for _ in range(10)])))])
  return dict(finish_reason='stop',content='candidates',tool_calls=[])
 cfg=Config(llm_base_url='http://localhost/v1',llm_api_key='test',llm_model='fake',llm_max_tokens=64,llm_temperature=0,llm_timeout=5)
 result=run_loop(cfg,'analyze',chat=chat,store=store,session_id=sid,viewer_snapshot=dict(version=1,visual_inputs=[mapping]),on_step=events.append)
 assert result['ok']
 tool=next(m for m in store.get_messages(sid) if m.get('tool_call_id')=='spatial-call')
 restored=_project_message(3,tool,{'spatial-call':'present_spatial_annotations'})
 assert len(json.loads(restored['content'])['data']['spatial_annotations'])==10
 event=next(e for e in events if e['type']=='tool_result')
 assert json.loads(restored['content'])['data']==event['data']
 store.close()


def test_server_resource_revision_changes_on_replacement(tmp_path):
 from backend.services.spatial_identity import resource_identity
 p=tmp_path/'scene.tif';p.write_bytes(b'old')
 first=resource_identity(p)
 assert resource_identity(p)==first
 p.write_bytes(b'replacement')
 assert resource_identity(p)!=first
 assert str(tmp_path) not in str(first)

class TestSpatialIdentityEndpoint(__import__('backend.tests.test_scene_resolve',fromlist=['ResolveBase']).ResolveBase):
 def test_guard_and_version(self):
  d=self.make_scene();c=self.client()
  row=c.post('/api/scenes/resolve',json={'path':self.win_path(d)}).json()['row']
  url='/api/scenes/'+row['id']+'/spatial-identity'
  first=c.get(url);self.assertEqual(first.status_code,200,first.text)
  self.assertEqual(c.get(url).json(),first.json())
  self.assertEqual(c.get('/api/scenes/invalid/spatial-identity').status_code,404)


def test_full_extent_edges_and_normalized_coordinates(mapping):
 tool=bound_tool([mapping])
 def args(coords,kind='bbox'):return dict(input_ref='actual',annotations=[dict(type=kind,coordinates=coords,title='candidate',summary='unverified')])
 result=tool.run(**args([0,0,1000,500]))
 assert result['ok'],result
 assert result['data']['spatial_annotations'][0]['geometry']['coordinates']==[2000,1000,6000,3000]
 for space,p in [('normalized_01',[1,1]),('normalized_1000',[1000,1000])]:
  result=tool.run(**args(p,'point'),coordinate_space=space)
  assert result['ok'],result
  assert result['data']['spatial_annotations'][0]['geometry']['coordinates']==[5996,2996]
 bad=tool.run(**args([12000,8000],'point'))
 assert not bad['ok']
 assert '1000x500' in bad['error'] and 'annotation[0]' in bad['error']
 assert not tool.run(**args([0,0,1001,500]))['ok']
 assert not tool.run(**args([1001,500],'point'),coordinate_space='normalized_1000')['ok']
 assert not tool.run(**args([1,1],'point'),coordinate_space='source_pixel')['ok']
