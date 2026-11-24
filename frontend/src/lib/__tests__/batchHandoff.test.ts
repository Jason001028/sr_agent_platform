// @vitest-environment jsdom
import { expect, it, vi } from 'vitest';
const fixture = vi.hoisted(() => ({ rows: [] as any[] }));
vi.mock('../../stores/batchGoal', () => ({ useBatchGoalStore: () => ({ goalId: null, snapshot: null }) }));
vi.mock('../../stores/batchDraft', () => ({ useBatchDraftStore: () => ({ candidates: fixture.rows, title: 'fixture', mode: 'demo', strategy: 'review' }) }));
import { AGENT_REPAIR_PROMPT, handoffBatch, requestAgentRepair, type BatchAgentHandoff } from '../batchHandoff';
import { agentRepairSettings } from '../agentRepairSettings';
it('compacts repeated evidence without dropping identities, states or validation issues', () => {
  fixture.rows = Array.from({ length: 50 }, (_, i) => ({ name: `image-${i}`, path: `/scene-${i}/PAN.tif`, evidence: '需要人工核对的同一条检测依据。'.repeat(20), status: '待确认', validationError: i === 3 ? '来源待核' : undefined }));
  let text = '';
  window.addEventListener('batch-agent-handoff', e => { text = (e as CustomEvent).detail.text; }, { once: true });
  handoffBatch('draft');
  const context = JSON.parse(text.split('\n').at(-1)!);
  expect(context.total).toBe(50);
  expect(context.candidates).toHaveLength(50);
  expect(context.evidence).toHaveLength(1);
  for (let i = 0; i < 50; i++) {
    expect(context.candidates[i][1]).toBe(fixture.rows[i]!.path);
    expect(context.evidence[context.candidates[i][3]]).toBe(fixture.rows[i]!.evidence);
  }
  expect(context.candidates[3][4]).toBe('来源待核');
  expect(text.length).toBeLessThan(JSON.stringify(fixture.rows).length / 2);
  expect(text).toContain('不提交 SR');
});

it('requests one auto-sent Agent repair that starts when the plan is ready', () => {
  let detail: Partial<BatchAgentHandoff> = {};
  window.addEventListener('batch-agent-handoff', e => {
    detail = (e as CustomEvent).detail;
  }, { once: true });
  requestAgentRepair();
  expect(detail.autoSend).toBe(true);
  expect(detail.text).toContain(AGENT_REPAIR_PROMPT);
  expect(detail.text).toContain('prepare_repair');
  expect(detail.text).toContain('mode=production');
  expect(detail.text).toContain('不切换模拟');
  expect(detail.text).toContain('不要直接提交 SR');
  expect(detail.text).toContain('准备完成后由界面自动启动');
  expect(detail.repairSettings?.auto_approve_mask).toBe(true);
  expect(detail.text).not.toContain('start_repair');
});

it('uses the same frozen inspection settings for the prompt and automatic send', () => {
  const previous = agentRepairSettings.value;
  agentRepairSettings.value = { ...previous, inspection_only: true, detection_method: 'smart' };
  try {
    let detail: any;
    window.addEventListener('batch-agent-handoff', e => { detail = (e as CustomEvent).detail; }, { once: true });
    const isCurrent = () => true;
    requestAgentRepair(isCurrent);
    agentRepairSettings.value.inspection_only = false;
    expect(detail.repairSettings).toMatchObject({ inspection_only: true, detection_method: 'smart' });
    expect(detail.text).toContain('inspect_repair_workspace');
    expect(detail.isCurrent).toBe(isCurrent);
  } finally {
    agentRepairSettings.value = previous;
  }
});
