import { expect, it } from 'vitest';
import type { ChatMsg } from '../../stores/chat';
import { projectAgentActivity } from '../agentActivity';
const user: ChatMsg = {seq:1,role:'user',content:'work'};
const calls = (...ids: string[]): ChatMsg => ({seq:2,role:'assistant',content:'',toolCalls:ids.map(id=>({id,name:'read_tool_result',args:{path:id}}))});
const result = (id: string, ok = true): ChatMsg => ({seq:null,role:'tool',content:'',toolName:'read_tool_result',toolCallId:id,ok,detail:ok?'done':'bad pointer'});

it('associates repeated names and concurrent outcomes by ID, not name or order', () => {
  const rows = [user,calls('a','b','c'),result('b',false),result('a')];
  const p = projectAgentActivity(rows,true);
  expect(p.steps.map(s=>s.state)).toEqual(['success','failed','running']);
  expect(p.steps[1].args).toEqual({path:'b'});
  expect([...p.groups.values()][0][0].steps).toHaveLength(3);
  expect(projectAgentActivity(rows,false).steps[2].state).toBe('interrupted');
});
it('deduplicates SSE replay declarations/results and keeps stable history keys', () => {
  const p = projectAgentActivity([user,calls('a'),result('a'),calls('a'),result('a')],false);
  expect(p.steps).toHaveLength(1);
  const history = [user,calls('a'),{...result('a'),seq:3}];
  expect(projectAgentActivity(history,false).steps[0].key).toBe(p.steps[0].key);
});
it('matches legacy calls FIFO without merging legitimate identical operations', () => {
  const call: ChatMsg = {seq:2,role:'assistant',content:'',toolCalls:[{name:'read_skill',args:{}},{name:'read_skill',args:{}}]};
  const first: ChatMsg = {seq:3,role:'tool',content:'',toolName:'read_skill',ok:false};
  const second = {...first,seq:4,ok:true};
  const p = projectAgentActivity([user,call,first,second,{...second}],false);
  expect(p.steps.map(s=>s.state)).toEqual(['failed','success']);
  const live = projectAgentActivity([user,{...call,seq:null},{...first,seq:null},{...second,seq:null}],false);
  expect(live.steps.map(s=>s.key)).toEqual(p.steps.map(s=>s.key));
});
it('does not guess success for missing results or connect mismatched explicit IDs', () => {
  const p = projectAgentActivity([user,calls('a'),result('unknown')],false);
  expect(p.steps.map(s=>s.state)).toEqual(['interrupted','success']);
  expect(projectAgentActivity([user,calls('a'),{...result('a'),ok:undefined}],false).steps[0].state).toBe('interrupted');
});
it('keeps business failure authoritative over a successful transport wrapper', () => {
  const p = projectAgentActivity([user,calls('a'),{...result('a'),data:{ok:false,error:'denied'}}],false);
  expect(p.steps[0].state).toBe('failed');
});
it('does not duplicate records if a replay result precedes its declaration', () => {
  expect(projectAgentActivity([user,result('a'),calls('a')],false).steps).toHaveLength(1);
});
it('scopes reused IDs to user turns and ends unfinished calls after a response', () => {
  const p = projectAgentActivity([user,calls('a'),{seq:3,role:'assistant',content:'finished'},
    {...user,seq:4},calls('a')],true);
  expect(p.steps.map(s=>s.state)).toEqual(['interrupted','running']);
  expect(p.steps[0].key).not.toBe(p.steps[1].key);
});
it('retains important results separately and does not repeat their contents in the timeline', () => {
  const messages: ChatMsg[] = [user,{seq:2,role:'assistant',content:'',toolCalls:[
    {id:'p',name:'prepare_repair',args:{}},{id:'s',name:'search_scenes',args:{}},{id:'r',name:'draft',args:{}},
    {id:'a',name:'present_spatial_annotations',args:{}},{id:'q',name:'request_clarification',args:{}},
  ]}, ...[
    ['p','prepare_repair',{kind:'repair_plan.v1',plan_id:'plan-one'}],
    ['s','search_scenes',{count:1,results:[{name:'candidate'}]}],
    ['r','draft',{kind:'batch_repair_draft.v1',candidates:[{name:'scene',path:'/s'}]}],
    ['a','present_spatial_annotations',{annotations:[]}],['q','request_clarification',{}],
  ].map(([id,name,data],n)=>({seq:n+3,role:'tool',content:'',toolCallId:id,toolName:name,ok:true,data} as ChatMsg))];
  const before = JSON.stringify(messages), p = projectAgentActivity(messages,false);
  expect([...p.business.values()].flat().map(s=>s.business)).toEqual(['plan','scenes','receipt','annotation','confirmation']);
  expect(p.groups.size).toBe(0);
  expect(JSON.stringify(messages)).toBe(before);
});
it('keeps failed searches visible and groups across tool-only assistant messages', () => {
  const a = calls('a'), b = {...calls('b'),seq:4};
  expect(projectAgentActivity([user,a,result('a'),b,result('b')],false).groups.size).toBe(1);
  expect(projectAgentActivity([user,a,result('a'),{seq:4,role:'assistant',content:'explanation'},b,result('b')],false).groups.size).toBe(2);
  const p=projectAgentActivity([user,{...a,toolCalls:[{id:'a',name:'search_scenes',args:{}}]},
    {...result('a',false),toolName:'search_scenes'}],false);
  expect(p.steps[0].business).toBeNull(); expect(p.steps[0].state).toBe('failed');
});
