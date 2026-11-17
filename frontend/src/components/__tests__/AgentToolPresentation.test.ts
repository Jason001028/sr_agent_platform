// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue';
import { expect, it, vi } from 'vitest';
import type { ChatMsg } from '../../stores/chat';
import { projectAgentActivity } from '../../lib/agentActivity';
vi.mock('../AgentRepairPlanCard.vue',()=>({default:{props:['data'],render(){return h('section',{'data-business':'plan'},'修复计划')}}}));
vi.mock('../SceneSearchResultCard.vue',()=>({default:{props:['data'],emits:['open'],render(this:{$emit:(event:string,value:unknown)=>void}){return h('button',{'data-business':'scenes',onClick:()=>this.$emit('open',{name:'candidate'})},'打开候选')}}}));
vi.mock('../AgentBatchReceipt.vue',()=>({default:{render(){return h('section',{'data-business':'receipt'},'任务回执')}}}));
vi.mock('../AgentAnnotationReference.vue',()=>({default:{render(){return h('section',{'data-business':'annotation'},'空间批注')}}}));
import AgentToolPresentation from '../AgentToolPresentation.vue';

it('preserves actionable business components without an extra ordinary trace',async()=>{
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
  expect(el.querySelector('.activity-timeline')).toBeNull();
  el.querySelector<HTMLButtonElement>('button')!.click();
  expect(open).toHaveBeenCalledWith({name:'candidate'});
  expect(el.querySelector('pre')).toBeNull();
  app.unmount();el.remove();
});
