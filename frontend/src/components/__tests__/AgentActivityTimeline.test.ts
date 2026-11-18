// @vitest-environment jsdom
import { createApp, h, nextTick, reactive } from 'vue';
import { afterEach, expect, it } from 'vitest';
import type { App } from 'vue';
import AgentActivityTimeline from '../AgentActivityTimeline.vue';
import type { ActivityGroup, ActivityState } from '../../lib/agentActivity';
import { activityDisclosure } from '../../lib/activityDisclosure';
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
  expect(el.textContent).toContain('已完成 2 项操作'); expect(el.querySelectorAll('li')).toHaveLength(0);
  el.querySelector<HTMLButtonElement>('button')!.click(); await flush();
  expect(el.querySelectorAll('li')).toHaveLength(2); expect(el.querySelector('pre')).toBeNull();
});
it('auto-collapses on completion but never overrides a manual expansion or close', async()=>{
  const state=mount(['success','running']); await flush();
  expect(el.querySelector('button')?.getAttribute('aria-expanded')).toBe('true');
  state.group=group(['success','success']); await flush();
  expect(el.querySelector('button')?.getAttribute('aria-expanded')).toBe('false');
  el.querySelector<HTMLButtonElement>('button')!.click(); state.group=group(['success','success','success']); await flush();
  expect(el.querySelector('button')?.getAttribute('aria-expanded')).toBe('true');
  el.querySelector<HTMLButtonElement>('button')!.click(); state.group=group(['success','running']); await flush();
  expect(el.querySelector('button')?.getAttribute('aria-expanded')).toBe('false');
  expect(el.querySelector('[data-state=running]')).not.toBeNull();
});
it('pins failures and unknown outcomes when collapsed instead of inventing green checks', async()=>{
  mount(['success','failed','interrupted']); await flush();
  expect(el.querySelectorAll('li')).toHaveLength(2);
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
