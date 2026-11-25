// @vitest-environment jsdom
import { createApp, h, nextTick, reactive } from 'vue';
import { afterEach, expect, it } from 'vitest';
import type { App } from 'vue';
import AgentActivityTimeline from '../AgentActivityTimeline.vue';
import type { ActivityGroup, ActivityState } from '../../lib/agentActivity';
import { activityDisclosure } from '../../lib/activityDisclosure';
import { projectAgentActivity } from '../../lib/agentActivity';
import { reduceChatSse, type ChatMsg } from '../../stores/chat';
let app: App | undefined, el: HTMLDivElement;
const group = (states: ActivityState[]): ActivityGroup => ({key:'stable',steps:states.map((state,n)=>({key:`s${n}`,name:'read_tool_result',label:'读取工具结果',args:{secret:'params'},callId:`id${n}`,callIndex:1,state,summary:state === 'failed' ? '结果读取失败，任务状态以业务卡片为准' : '执行完成',business:null}))});
async function flush() { await nextTick(); await new Promise(resolve=>setTimeout(resolve,240)); await nextTick(); }
function mount(states: ActivityState[]) {
  el=document.createElement('div'); document.body.append(el);
  const state=reactive({group:group(states),sessionKey:'s',developerMode:false});
  app=createApp(()=>h(AgentActivityTimeline,state)); app.mount(el); return state;
}
afterEach(()=>{app?.unmount();el?.remove();activityDisclosure.clear();});
it('collapses successful groups and expands exact steps on demand', async()=>{
  mount(['success','success']); await flush();
  expect(el.textContent).toContain('已完成 2 项操作'); expect(el.querySelectorAll('li:not([hidden])')).toHaveLength(0);
  el.querySelector<HTMLButtonElement>('button')!.click(); await flush();
  expect(el.querySelectorAll('li')).toHaveLength(2); expect(el.querySelector('pre')).toBeNull();
});
it('auto-collapses on completion but never overrides a manual expansion or close', async()=>{
  const state=mount(['success','running']); await flush();
  expect(el.querySelector('button')?.getAttribute('aria-expanded')).toBe('true');
  const running=el.querySelector('[data-state=running]');
  state.group=group(['success','success']); await flush();
  expect(el.querySelectorAll('[data-state=success]')[1]).toBe(running);
  expect(el.querySelector('button')?.getAttribute('aria-expanded')).toBe('false');
  el.querySelector<HTMLButtonElement>('button')!.click(); state.group=group(['success','success','success']); await flush();
  expect(el.querySelector('button')?.getAttribute('aria-expanded')).toBe('true');
  el.querySelector<HTMLButtonElement>('button')!.click(); state.group=group(['success','running']); await flush();
  expect(el.querySelector('button')?.getAttribute('aria-expanded')).toBe('false');
  expect(el.querySelector('[data-state=running]')).not.toBeNull();
});
it('pins failures and unknown outcomes when collapsed instead of inventing green checks', async()=>{
  mount(['success','failed','interrupted']); await flush();
  expect(el.querySelectorAll('li:not([hidden])')).toHaveLength(2);
  expect(el.querySelector('button')?.textContent).toContain('1 项失败 · 1 项结果未确认');
  expect(el.textContent).toContain('结果读取失败');
  expect(el.querySelector('.state-icon.success')).toBeNull();
  expect(el.querySelector('[data-state=interrupted]')).not.toBeNull();
});
it('retains user preference after history replacement and scopes it to the session', async()=>{
  const state=mount(['success','success']); await flush();
  el.querySelector<HTMLButtonElement>('button')!.click(); await flush();
  app!.unmount(); app=createApp(()=>h(AgentActivityTimeline,state)); app.mount(el); await flush();
  expect(el.querySelector('button')?.getAttribute('aria-expanded')).toBe('true');
  state.sessionKey='another'; await flush();
  expect(el.querySelector('button')?.getAttribute('aria-expanded')).toBe('false');
  state.developerMode=true; el.querySelector<HTMLButtonElement>('button')!.click(); await flush();
  expect(el.textContent).toContain('开发者详情'); expect(el.querySelector('pre')?.textContent).toContain('params');
});

it('updates an inspection row in place from waiting through detection to its receipt', async () => {
  const messages = reactive<ChatMsg[]>([{ seq: 1, role: 'user', content: '质检' }]);
  const sending = reactive({ value: true });
  const apply = (event: Parameters<typeof reduceChatSse>[1]) =>
    messages.splice(0, messages.length, ...reduceChatSse(messages, event));
  apply({ type: 'tool_call', id: 'inspection', name: 'inspect_repair_workspace', args: { cursor: 5 } });
  messages[1].toolCalls![0].startedAt = Date.now() - 5000;
  el = document.createElement('div');
  app = createApp(() => h(AgentActivityTimeline, {
    group: projectAgentActivity(messages, sending.value).groups.get(1)![0],
  }));
  app.mount(el);
  const line = el.querySelector('li')!;
  expect(line.textContent).toContain('从第 6 景起');
  expect(line.textContent).toContain('已等待 5 秒');
  apply({ type: 'tool_progress', id: 'inspection', phase: 'detecting_scene', position: 6, total: 9, scene: '影像 A' });
  await nextTick();
  expect(line.textContent).toContain('影像 A');
  expect(line.textContent).toContain('正在读取 PAN / NOSR 并检测');
  apply({ type: 'tool_result', id: 'inspection', name: 'inspect_repair_workspace', ok: true,
    error: null, duration_ms: 6020, data: { kind: 'repair_inspection.v1', total: 9, cursor: 5, has_more: true,
      items: [{ status: 'candidates' }] } });
  await nextTick();
  expect(el.querySelector('li')).toBe(line);
  expect(line.textContent).toContain('1 景有候选');
  expect(line.textContent).toContain('6 秒');
});

it('keeps a successful query that found blocked scenes visible in a folded group', () => {
  const state = mount(['success','success']);
  const steps = projectAgentActivity([
    { seq: 1, role: 'user', content: '查批次' },
    { seq: 2, role: 'tool', content: '', ok: true, toolName: 'query_batch_repair', data: { counts: { blocked: 2 } } },
    { seq: 3, role: 'tool', content: '', ok: true, toolName: 'get_current_image_info' },
  ], false).groups.get(1)![0].steps;
  state.group = { key: 'stable', steps };
  return nextTick().then(() => {
    expect(el.querySelector('.group-toggle')?.getAttribute('aria-expanded')).toBe('false');
    expect(el.querySelector('li:not([hidden])')?.textContent).toContain('2 景受阻');
  });
});
