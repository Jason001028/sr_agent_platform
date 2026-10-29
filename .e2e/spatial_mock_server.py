"""Test-only scripted model. Temporary DB/images; no real model or production records."""
import os,sys,tempfile,socket,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
root=tempfile.TemporaryDirectory()
os.environ.update(SR_AGENT_DB=str(Path(root.name)/'test.db'),SR_CHAT_IMAGE_DIR=str(Path(root.name)/'images'),SR_LLM_MOCK='0',SR_LLM_BASE_URL='http://unused/v1')
os.environ.pop('SR_SPATIAL_GROUNDING',None)
from backend.agent import loop
from backend.api.app import create_app
import uvicorn

def factory(cfg,**kwargs):
 def chat(messages,tools,**options):
  current=next(m for m in reversed(messages) if m['role']=='user')
  blocks=current['content']
  if any(b.get('type')=='text' and 'TEXT_ONLY' in b.get('text','') for b in blocks):return dict(content='Mock text only: no reliable location.',tool_calls=[],finish_reason='stop')
  observation=next(b['text'] for b in blocks if b.get('type')=='text' and b.get('text','').startswith('Viewer observation'))
  m=json.loads(observation.split('\n',1)[1])['visual_inputs'][0]
  if messages[-1]['role']=='tool':return dict(content='Mock spatial candidates; not real analysis.',tool_calls=[],finish_reason='stop')
  assert any(t['function']['name']=='present_spatial_annotations' for t in tools)
  assert any(b.get('type')=='image_url' for b in blocks)
  w,h=m['width'],m['height']
  annotations=[dict(type='point',coordinates=[w*.25,h*.3],title='Mock point',summary='Mock fixture only'),dict(type='bbox',coordinates=[w*.45,h*.35,w*.6,h*.5],title='Mock bbox',summary='Mock fixture only'),dict(type='polygon',coordinates=[[w*.65,h*.6],[w*.8,h*.65],[w*.72,h*.8]],title='Mock polygon',summary='Mock fixture only')]
  return dict(content='',finish_reason='tool_calls',tool_calls=[dict(id='mock-spatial',name='present_spatial_annotations',arguments=json.dumps(dict(input_ref=m['input_ref'],annotations=annotations)))])
 return chat
loop._default_chat=factory
original_run=loop.run_loop
def duplicate_delivery(*args,**kwargs):
 step=kwargs.get('on_step')
 if step:
  def twice(event):
   step(event)
   if event.get('type')=='tool_result':step(event)
  kwargs['on_step']=twice
 return original_run(*args,**kwargs)
loop.run_loop=duplicate_delivery
sock=socket.socket();sock.bind(('127.0.0.1',0));print(sock.getsockname()[1],flush=True)
uvicorn.Server(uvicorn.Config(create_app(),log_level='error')).run(sockets=[sock])
