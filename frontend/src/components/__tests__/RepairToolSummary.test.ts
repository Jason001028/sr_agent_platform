// @vitest-environment jsdom
import { createApp, nextTick } from 'vue';
import { expect, it } from 'vitest';
import RepairToolSummary from '../RepairToolSummary.vue';

it('shows business failures even if a wrapper claims transport success and hides raw JSON', async () => {
  const el = document.createElement('div'); document.body.append(el);
  const app = createApp(RepairToolSummary, {name:'read_tool_result',result:{seq:null,role:'tool',content:'',ok:true,
    data:{ok:false,error:'missing pointer',data:{plan_id:'secret-plan-id',next_offset:20}}}});
  app.mount(el); await nextTick();
  expect(el.querySelector('[data-state=failed]')).not.toBeNull();
  expect(el.textContent).toContain('读取失败');
  expect(el.textContent).not.toContain('已完成');
  expect(el.textContent).not.toContain('secret-plan-id');
  expect(el.querySelector('pre')).toBeNull();
  app.unmount(); el.remove();
});
