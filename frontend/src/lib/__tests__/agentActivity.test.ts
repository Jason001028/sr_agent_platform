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
it('updates a repeated repair plan in its original card position', () => {
  const first = { ...result('p'), data: { kind: 'repair_plan.v1', plan_id: 'plan-one', version: 1 } };
  const latest = { ...result('q'), data: { ...first.data, version: 2 } };
  const messages = [user, calls('p'), first];
  const original = projectAgentActivity(messages, true).business.get(2)![0];
  const updated = projectAgentActivity([...messages, calls('q'), latest], false).business;
  expect([...updated.keys()]).toEqual([2]);
  expect(updated.get(2)![0]).toMatchObject({ key: original.key, result: latest });
});
it('retains fresh launch intent when the model rereads the same plan revision', () => {
  const data = { kind: 'repair_plan.v1', plan_id: 'plan-one', version: 1 };
  const prepared: ChatMsg = { ...result('p'), toolName: 'prepare_repair', autoStartRepair: true, data };
  const read = { ...result('q'), data };
  const rows = [user, prepared, read];
  expect(projectAgentActivity(rows, false).business.get(1)?.[0].result?.autoStartRepair).toBe(true);
  const revised = { ...read, data: { ...data, version: 2 } };
  expect(projectAgentActivity([user, prepared, revised], false).business.get(1)?.[0].result?.autoStartRepair).toBeUndefined();
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
  expect([...p.groups.values()].flatMap(groups => groups.flatMap(group => group.steps.map(step => step.name))))
    .toEqual(['prepare_repair', 'search_scenes', 'draft', 'present_spatial_annotations', 'request_clarification']);
  expect(JSON.stringify(messages)).toBe(before);
});
it('keeps a blocked plan on the original tool row and projects its card before the reply', () => {
  const declaration: ChatMsg = { seq: 2, role: 'assistant', content: '', toolCalls: [{ id: 'p', name: 'prepare_repair', args: {} }] };
  const plan = { ...result('p'), toolName: 'prepare_repair', data: { kind: 'repair_plan.v1', plan_id: 'p', status: 'BLOCKED' } };
  const active = projectAgentActivity([user, declaration], true);
  const received = projectAgentActivity([user, declaration, plan], true);
  expect(received.groups.get(1)?.[0].steps[0]).toMatchObject({
    key: active.steps[0].key, summary: '计划受阻', resultLink: { key: active.steps[0].key, label: '查看计划' },
  });
  expect(received.business.get(2)?.[0].business).toBe('plan');
});
it('reports inspection progress by scene position instead of the batch total', () => {
  const page = (cursor: number, count: number, hasMore: boolean): ChatMsg => ({
    seq: null, role: 'tool', content: '', toolName: 'inspect_repair_workspace',
    toolCallId: `qc${cursor}`, ok: true,
    data: { kind: 'repair_inspection.v1', total: 27, cursor, has_more: hasMore,
      items: Array.from({ length: count }, (_, index) => ({ item_id: `i${cursor + index}` })) },
  });
  const call = (id: string): ChatMsg => ({ seq: 2, role: 'assistant', content: '',
    toolCalls: [{ id, name: 'inspect_repair_workspace', args: {} }] });
  expect(projectAgentActivity([user, call('qc0'), page(0, 5, true)], false).steps[0].summary)
    .toBe('5/27 景…');
  expect(projectAgentActivity([user, call('qc25'), page(25, 2, false)], false).steps[0].summary)
    .toBe('27/27 景');
});
it('keeps separate reports for repeated inspections and preserves rows from an unfinished run', () => {
  const page = (seq: number, cursor: number, name: string): ChatMsg => ({
    seq, role: 'tool', content: '', ok: true, toolName: 'inspect_repair_workspace',
    data: { kind: 'repair_inspection.v1', total: 2, cursor, has_more: cursor === 0,
      scope: 'workspace', detection_method: 'smart', tone_weight: false, items: [{ name }] },
  });
  const messages = [user, page(2, 0, 'A'), page(3, 1, 'B'), page(4, 0, 'C')];
  const report = (sending: boolean) => [...projectAgentActivity(messages, sending).business.values()]
    .flat().map(step => step.inspection);
  expect(report(true)).toMatchObject([{ partial: false, items: [{ name: 'A' }, { name: 'B' }] }]);
  expect(report(false)).toMatchObject([
    { partial: false, items: [{ name: 'A' }, { name: 'B' }] },
    { partial: true, items: [{ name: 'C' }], total: 2, detection_method: 'smart', tone_weight: false },
  ]);
  messages.push(page(5, 1, 'D'));
  expect(report(false)[1]).toMatchObject({ partial: false, items: [{ name: 'C' }, { name: 'D' }] });
});
it('shows the business counts returned by a batch status query', () => {
  const query: ChatMsg = { ...result('q'), toolName: 'query_batch_repair',
    data: { counts: { failed: 3, total: 27 }, quality: { waiting_scenes: 2 }, active_jobs_total: 4 } };
  expect(projectAgentActivity([user, query], false).steps[0]).toMatchObject({
    state: 'success', label: '查询修复进度', summary: '3 景失败 · 2 景待人工验收 · 4 个作业进行中',
  });
});
it('keeps failed searches visible and groups across tool-only assistant messages', () => {
  const a = calls('a'), b = {...calls('b'),seq:4};
  expect(projectAgentActivity([user,a,result('a'),b,result('b')],false).groups.size).toBe(1);
  expect(projectAgentActivity([user,a,result('a'),{seq:4,role:'assistant',content:'explanation'},b,result('b')],false).groups.size).toBe(2);
  const p=projectAgentActivity([user,{...a,toolCalls:[{id:'a',name:'search_scenes',args:{}}]},
    {...result('a',false),toolName:'search_scenes'}],false);
  expect(p.steps[0].business).toBeNull(); expect(p.steps[0].state).toBe('failed');
});
