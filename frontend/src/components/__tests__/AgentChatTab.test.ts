// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { computed, createApp, h, nextTick, reactive } from 'vue';
import type { App } from 'vue';

/* 侧舱 Agent tab 的输入区解锁。这里的重点是「没有已加载影像」的场景：点击
 * 质检清单的 Agent协助 后，输入区不该继续被 .at-input-lock 磨砂遮罩盖住。 */
const state = vi.hoisted(() => ({ chat: {} as any, viewer: {} as any, annotations: {} as any, togglePause: vi.fn(), control: {} as any }));
vi.mock('../../stores/viewer', () => ({ useViewerStore: () => state.viewer }));
vi.mock('../../stores/chat.js', () => ({ useChatStore: () => state.chat }));
vi.mock('../../stores/chat', () => ({ useChatStore: () => state.chat }));
vi.mock('../../stores/scenes', () => ({ useScenesStore: () => ({ openByName: vi.fn() }) }));
vi.mock('../../stores/annotations', () => ({ useAnnotationsStore: () => state.annotations }));
state.annotations = reactive({ all: [], demo: [], hidden: false, status: '', selected: null, hovered: null });
// 会话一挂上就会去拉澄清项；本用例不测那条网络链路。
vi.mock('../GoalClarifications.vue', () => ({ default: { setup: () => () => null } }));
vi.mock('../AgentRepairPlanCard.vue', () => ({
  default: {
    props: { data: Object, standalone: Boolean }, emits: ['back'],
    setup(props: { data: { plan_id: string }; standalone: boolean }, { emit, expose }: { emit: (name: 'back') => void; expose: (exposed: object) => void }) {
      expose({ taskControl: computed(() => state.control[props.data.plan_id]), togglePause: state.togglePause });
      return () => h('button', { 'data-plan-id': props.data.plan_id, onClick: () => emit('back') },
        props.standalone ? '修复待办' : '修复计划');
    },
  },
}));

import Tab from '../AgentChatTab.vue';
import { agentTaskView } from '../../lib/agentTaskView';

let app: App;
let root: HTMLDivElement;

beforeEach(() => {
  agentTaskView.value = 'card';
  state.control = reactive({});
  state.togglePause.mockClear();
  // AgentBotIcon 用 matchMedia 判断减少动态；jsdom 没有，给个静态替身。
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
      addListener: vi.fn(), removeListener: vi.fn() })),
  });
  state.viewer = reactive({
    activeRec: null, activeStretch: 'linear', settingsOpen: false, panes: [], recs: [],
    questionRegion: null, roiStats: null, roiSelIndex: () => -1,
  });
  state.chat = reactive({
    messages: [], sending: false, sessionId: 's1', sessions: [], error: '',
    agentStatus: 'idle', pendingImages: [], thinkingProfile: 'standard', thinkingStatus: '',
    activity: { label: '' }, streamTick: 0,
    refreshSessions: vi.fn(), newSession: vi.fn(), selectSession: vi.fn(),
    deleteSession: vi.fn(), send: vi.fn(), loadHistory: vi.fn(),
    addPendingImage: vi.fn(), removePendingImage: vi.fn(),
  });
  root = document.createElement('div');
  document.body.append(root);
  app = createApp(Tab);
  app.component('router-link', { render: () => h('a') });
  app.mount(root);
});

afterEach(() => { app.unmount(); root.remove(); });

function handoff(autoSend: boolean): void {
  window.dispatchEvent(new CustomEvent('batch-agent-handoff', { detail: {
    text: '请准备修复计划',
    autoSend,
    repairSettings: autoSend ? { inspection_only: true, detection_method: 'smart' } : undefined,
  } }));
}

it('locks the composer while no image is loaded', () => {
  expect(root.querySelector('.at-input-lock')).not.toBeNull();
  expect(root.querySelector('.at-foot')!.classList.contains('locked')).toBe(true);
});

it('unlocks the composer on the auto-sent Agent协助 handoff', async () => {
  handoff(true);
  await nextTick();
  expect(root.querySelector('.at-input-lock')).toBeNull();
  expect(root.querySelector('.at-foot')!.classList.contains('locked')).toBe(false);
  expect(state.chat.send).toHaveBeenCalledTimes(1);
});

it('keeps the composer unlocked after sending the handed-off context', async () => {
  handoff(true);
  await nextTick();
  state.chat.send.mockClear();
  const input = root.querySelector<HTMLInputElement>('.at-input')!;
  expect(input.disabled).toBe(false);
  input.value = '继续追问';
  input.dispatchEvent(new Event('input'));
  await nextTick();   // 等发送键从 disabled 刷新出来
  root.querySelector<HTMLButtonElement>('.cbtn')!.click();
  await nextTick();
  expect(state.chat.send).toHaveBeenCalledTimes(1);
  expect(root.querySelector('.at-input-lock')).toBeNull();
});

it('renders the task card directly and switches to the standalone view for the same plan', async () => {
  state.chat.sending = true;
  state.chat.messages = [
    { seq: 1, role: 'user', content: '准备修复计划' },
    { seq: 2, role: 'assistant', content: '', toolCalls: [{ id: 'p', name: 'prepare_repair', args: {} }] },
    { seq: 3, role: 'tool', content: '', toolCallId: 'p', toolName: 'prepare_repair', ok: true,
      data: { kind: 'repair_plan.v1', plan_id: 'p' } },
  ];
  await nextTick();
  expect(root.querySelector('.agent-task-pane')).toBeNull();
  expect(root.querySelector('[data-plan-id="p"]')?.textContent).toBe('修复计划');
  expect(root.textContent).not.toContain('打开修复待办');
  agentTaskView.value = 'standalone';
  await nextTick();
  expect(root.querySelector('.agent-task-pane [data-plan-id="p"]')?.textContent).toBe('修复待办');
  root.querySelector<HTMLButtonElement>('.agent-task-pane button')!.click();
  await nextTick();
  expect(root.querySelector('.agent-task-pane')).toBeNull();
  expect(root.querySelector('[data-plan-id="p"]')?.textContent).toBe('修复计划');
  state.chat.sessionId = 's2';
  state.chat.messages = [];
  await nextTick();
  expect(root.querySelector('.agent-task-pane')).toBeNull();
});

it('uses the composer button to pause and resume the default Agent assist card', async () => {
  handoff(true);
  state.chat.messages = [
    { seq: 1, role: 'assistant', content: '', toolCalls: [{ id: 'p', name: 'prepare_repair', args: {} }] },
    { seq: 2, role: 'tool', content: '', toolCallId: 'p', toolName: 'prepare_repair', ok: true,
      data: { kind: 'repair_plan.v1', plan_id: 'p' } },
  ];
  const button = () => root.querySelector<HTMLButtonElement>('.at-inputbar .cbtn')!;
  await nextTick();
  expect(button().getAttribute('aria-label')).toBe('发送');
  state.control.p = { icon: 'pause', label: '暂停执行', busy: false };
  await nextTick();
  expect(root.querySelector('.agent-task-pane')).toBeNull();
  expect(button().getAttribute('aria-label')).toBe('暂停执行');
  button().click();
  expect(state.togglePause).toHaveBeenCalledTimes(1);
  state.control.p = { icon: 'square', label: '正在暂停…', busy: true };
  await nextTick();
  expect(button().disabled).toBe(true);
  state.control.p = { icon: 'play', label: '继续执行', busy: false };
  await nextTick();
  expect(button().getAttribute('aria-label')).toBe('继续执行');
  button().click();
  expect(state.togglePause).toHaveBeenCalledTimes(2);
  state.chat.messages = [];
  await nextTick();
  expect(button().getAttribute('aria-label')).toBe('发送');
});
