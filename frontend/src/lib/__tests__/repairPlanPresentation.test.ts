import { expect, it } from 'vitest';
import { compactRepairTools, hasRepairPlanInTurn } from '../repairPlan';
import { msgFromHistory } from '../../stores/chat';
import type { ChatMsg } from '../../stores/chat';

it('folds prose only when the same user turn contains a real plan', () => {
  const messages: ChatMsg[] = [
    {seq:1,role:'user',content:'prepare'},
    {seq:2,role:'assistant',content:'plan_id in a markdown table'},
    {seq:3,role:'tool',toolName:'prepare_repair',content:'',data:{kind:'repair_plan.v1',plan_id:'p'}},
    {seq:4,role:'assistant',content:'long narrative'},
    {seq:5,role:'user',content:'explain a different topic'},
    {seq:6,role:'assistant',content:'normal answer'},
  ];
  expect(hasRepairPlanInTurn(messages,1)).toBe(true);
  expect(hasRepairPlanInTurn(messages,3)).toBe(true);
  expect(hasRepairPlanInTurn(messages,5)).toBe(false);
  expect(hasRepairPlanInTurn(messages.slice(0,2),1)).toBe(false);
});

it('coalesces result reads within one turn without merging different turns', () => {
  const messages: ChatMsg[] = [
    {seq:1,role:'assistant',content:'',toolCalls:[{id:'a',name:'read_tool_result',args:{}}]},
    {seq:2,role:'assistant',content:'',toolCalls:[{id:'b',name:'read_tool_result',args:{}}]},
    {seq:3,role:'user',content:'new question'},
    {seq:4,role:'assistant',content:'',toolCalls:[{id:'c',name:'read_tool_result',args:{}}]},
  ];
  expect(compactRepairTools(messages[0],messages,0)).toHaveLength(0);
  expect(compactRepairTools(messages[1],messages,1)[0].id).toBe('b');
});

it('restores the structured server plan without reading narrative', () => {
  const data = {kind:'repair_plan.v1',plan_id:'p',version:2,status:'STARTED'};
  expect(msgFromHistory({seq:1,role:'tool',tool_name:'prepare_repair',ok:true,content:null,data}).data).toEqual(data);
});
