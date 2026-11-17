// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApp, h, nextTick, reactive } from 'vue';
import type { App } from 'vue';
import ReasoningBlock from '../ReasoningBlock.vue';
import type { ChatPhase } from '../../lib/api';
import type { ReasoningTiming } from '../../lib/reasoningTrace';

let app: App | undefined;
let root: HTMLDivElement;
afterEach(() => { app?.unmount(); app = undefined; root?.remove(); vi.restoreAllMocks(); });

function mount(initial: Record<string, unknown>) {
  root = document.createElement('div');
  document.body.append(root);
  const state = reactive<Record<string, unknown>>({
    text: 'reasoning text', ...initial,
  });
  const timings: ReasoningTiming[] = [];
  app = createApp({
    render: () => h(ReasoningBlock, {
      ...state,
      onTiming: (value: ReasoningTiming) => timings.push(value),
    } as never),
  });
  app.mount(root);
  return { state, timings };
}
const title = () => root.querySelector('.reason-title')?.textContent ?? '';

describe('ReasoningBlock', () => {
  it('shows the running title with breathing dots and reports real seconds', async () => {
    let now = 0;
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    const { state, timings } = mount({ streaming: true, phase: 'thinking' as ChatPhase });
    expect(title()).toBe('深度思考中…');
    expect(root.querySelector('.dots')).not.toBeNull();

    now = 2400;
    state.phase = 'answering';
    await nextTick();
    expect(title()).toBe('已思考 2.4 秒');
    expect(timings.at(-1)).toEqual({ seconds: 2.4, segments: 1 });
  });

  it('stops the dots when the thinking phase ends', async () => {
    let now = 0;
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    const { state } = mount({ streaming: true, phase: 'thinking' as ChatPhase });
    now = 1000;
    state.phase = 'answering';
    await nextTick();
    await nextTick();
    // jsdom has no transitionend; the leaving class proves the dots are
    // fading out instead of being cut instantly.
    expect(root.querySelector('.dots')?.className)
      .toContain('reason-dots-leave-active');
  });

  it('falls back to 思考已结束 when no reliable timing exists', () => {
    mount({ streaming: false, phase: null });
    expect(title()).toBe('思考已结束');
  });

  it('renders a merged short-stage summary', () => {
    mount({ streaming: false, phase: null, mergedCount: 3, mergedSeconds: 8 });
    expect(title()).toBe('已完成 3 段思考 · 累计 8 秒');
  });

  it('auto-collapses on completion and lets the user expand again', async () => {
    let now = 0;
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    const { state } = mount({ streaming: true, phase: 'thinking' as ChatPhase });
    expect(root.querySelector('.reason-body')).toBeNull();
    now = 1200;
    state.phase = 'answering';
    await nextTick();
    expect(root.querySelector('.reason-body')).toBeNull();       // auto-collapsed
    root.querySelector<HTMLButtonElement>('.reason-toggle')!.click();
    await nextTick();
    expect(root.querySelector('.reason-body')?.textContent).toBe('reasoning text');
    expect(root.querySelector('.reason')?.className).toContain('done');
  });

  it('keeps a user-opened block open when the stage ends', async () => {
    let now = 0;
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    const { state } = mount({ streaming: true, phase: 'thinking' as ChatPhase });
    root.querySelector<HTMLButtonElement>('.reason-toggle')!.click();
    await nextTick();
    now = 900;
    state.phase = 'answering';
    await nextTick();
    expect(root.querySelector('.reason-body')?.textContent).toBe('reasoning text');
  });
});
