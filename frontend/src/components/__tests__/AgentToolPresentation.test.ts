// @vitest-environment jsdom
import { createApp, h, nextTick, ref } from 'vue';
import { expect, it, vi } from 'vitest';
import type { ChatMsg } from '../../stores/chat';
import { projectAgentActivity } from '../../lib/agentActivity';
const { jump } = vi.hoisted(() => ({ jump: vi.fn() }));
vi.mock('../../lib/inspectionJump', () => ({ jumpToInspectionScene: jump }));
vi.mock('../AgentRepairPlanCard.vue',()=>({default:{props:['data'],render(){return h('section',{'data-business':'plan'},'修复计划')}}}));
vi.mock('../SceneSearchResultCard.vue',()=>({default:{props:['data'],emits:['open'],render(this:{$emit:(event:string,value:unknown)=>void}){return h('button',{'data-business':'scenes',onClick:()=>this.$emit('open',{name:'candidate'})},'打开候选')}}}));
vi.mock('../AgentBatchReceipt.vue',()=>({default:{render(){return h('section',{'data-business':'receipt'},'任务回执')}}}));
vi.mock('../AgentAnnotationReference.vue',()=>({default:{render(){return h('section',{'data-business':'annotation'},'空间批注')}}}));
import AgentToolPresentation from '../AgentToolPresentation.vue';

it('renders one visible inspection table after all positions have arrived', async () => {
  const name = 'JL1KF02B02_PMS04_20261025120913_200548603_102_0024_001_L1_PAN';
  const page = (cursor: number, hasMore: boolean): ChatMsg => ({
    seq: cursor + 2, role: 'tool', content: '', ok: true, toolName: 'inspect_repair_workspace',
    data: { kind: 'repair_inspection.v1', total: 3, cursor, has_more: hasMore, scope: 'workspace',
      detection_method: 'smart', tone_weight: false,
      requested_total: 4, not_inspected: [{ name: '缺少配对影像', reason: '缺少 NOSR', kind: 'unresolved' }],
      items: [{ item_id: String(cursor), name, status: cursor ? 'candidates' : 'no_candidate', candidate_count: cursor }] },
  });
  const messages = ref<ChatMsg[]>([{ seq: 1, role: 'user', content: '质检' }, page(0, true), page(2, false)]);
  const el = document.createElement('div');
  const app = createApp(() => {
    const projection = projectAgentActivity(messages.value, true);
    return messages.value.map((_, index) => h(AgentToolPresentation, { key: index, projection, index }));
  });
  app.mount(el);
  expect(el.querySelector('.inspection-card')).toBeNull();
  messages.value.push(page(1, true));
  await nextTick();
  const table = el.querySelector('.inspection-card table')!;
  const card = el.querySelector('.inspection-card')!;
  expect(card.textContent).toContain('请求 4 景');
  expect(card.textContent).toContain('3 景已检测');
  expect(card.textContent).toContain('1 景未检测');
  expect(card.querySelector('details')?.textContent).toContain('缺少 NOSR');
  expect(table.closest('details')).toBeNull();
  expect([...table.querySelectorAll('th')].map(cell => cell.textContent)).toEqual(['景名 / 结果', 'PAN', 'NOSR', '差分结果']);
  expect([...table.querySelectorAll('tbody tr')].map(row => row.querySelector('td')?.textContent))
    .toEqual(['未检出候选', '1 处候选', '2 处候选'].map(outcome => name + outcome + '跳转至对应景'));
  const jumps = [...table.querySelectorAll<HTMLButtonElement>('tbody .jump-scene')];
  expect(jumps).toHaveLength(3);
  expect(jumps.map(button => button.textContent)).toEqual(['跳转至对应景', '跳转至对应景', '跳转至对应景']);
  expect(jumps[0]!.disabled).toBe(false);
  jumps[0]!.click();
  await vi.waitFor(() => expect(jump).toHaveBeenCalledWith(name, undefined, { method: 'smart', toneWeight: false }));
  expect(table.querySelector('.scene-name')?.getAttribute('title')).toBe(name);
  messages.value.push({ ...page(2, false), seq: 5 });
  await nextTick();
  expect(el.querySelectorAll('.inspection-card')).toHaveLength(1);
  expect(el.querySelector('.inspection-card table')).toBe(table);
  const dialog = el.querySelector('dialog')!;
  dialog.showModal = vi.fn();
  dialog.close = () => dialog.dispatchEvent(new Event('close'));
  el.querySelector<HTMLButtonElement>('.inspection-card > button')!.click();
  await nextTick();
  expect(dialog.showModal).toHaveBeenCalledOnce();
  expect(dialog.querySelector('table')).toBe(table);
  expect(el.querySelectorAll('.inspection-card table')).toHaveLength(1);
  dialog.querySelector('button')!.click();
  await nextTick();
  expect(dialog.querySelector('table')).toBeNull();
  expect(el.querySelector('.inspection-card table')).toBe(table);
  app.unmount();
});

it('keeps the original call group while showing actionable business results',async()=>{
  const messages: ChatMsg[] = [
    {seq:1,role:'user',content:'work'},
    {seq:2,role:'assistant',content:'',toolCalls:[{id:'p',name:'prepare_repair',args:{}},{id:'s',name:'search_scenes',args:{}}]},
    {seq:3,role:'tool',content:'',toolCallId:'p',toolName:'prepare_repair',ok:true,data:{kind:'repair_plan.v1',plan_id:'p'}},
    {seq:4,role:'tool',content:'',toolCallId:'s',toolName:'search_scenes',ok:true,data:{count:1,results:[{name:'candidate'}]}},
    {seq:5,role:'tool',content:'',toolName:'draft',ok:true,data:{kind:'batch_repair_draft.v1',candidates:[{name:'scene',path:'/s'}]}},
    {seq:6,role:'tool',content:'',toolName:'present_spatial_annotations',ok:true,data:{}},
  ];
  const projection=projectAgentActivity(messages,false), open=vi.fn();
  const el=document.createElement('div'); document.body.append(el);
  const app=createApp(()=>messages.map((_,index)=>h(AgentToolPresentation,{projection,index,onOpenScene:open})));
  app.mount(el); await nextTick();
  expect(el.querySelectorAll('[data-business]')).toHaveLength(4);
  expect(el.querySelectorAll('.activity-timeline')).toHaveLength(1);
  expect(el.querySelectorAll('.activity-step:not([hidden])')).toHaveLength(1);
  el.querySelector<HTMLButtonElement>('[data-business=scenes]')!.click();
  expect(open).toHaveBeenCalledWith({name:'candidate'});
  expect(el.querySelector('pre')).toBeNull();
  app.unmount();el.remove();
});
