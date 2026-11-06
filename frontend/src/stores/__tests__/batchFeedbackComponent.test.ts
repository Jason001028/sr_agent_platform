// @vitest-environment jsdom
import { createApp, h, nextTick, reactive } from 'vue';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { BatchFeedback } from '../../lib/batchUi';
const { claim } = vi.hoisted(() => ({ claim: vi.fn() }));
vi.mock('../batchGoal', () => ({ useBatchGoalStore: () => ({ claimFeedback: claim }) }));
import BatchFeedbackIcon from '../../components/BatchFeedbackIcon.vue';
let intersect: (entries: { isIntersecting: boolean }[]) => void;
let dispose: (() => void) | undefined;
beforeEach(() => {
  vi.useFakeTimers(); claim.mockReset(); const seen = new Set<string>();
  claim.mockImplementation((key: string) => { if (seen.has(key)) return false; seen.add(key); return true; });
  Object.defineProperty(document, 'hidden', { configurable: true, value: false });
  vi.stubGlobal('IntersectionObserver', class {
    constructor(cb: typeof intersect) { intersect = cb; }
    observe() {} disconnect() {}
  });
});
afterEach(() => { dispose?.(); dispose = undefined; vi.useRealTimers(); vi.unstubAllGlobals(); });
function mount(status = 'SUCCEEDED', cue?: BatchFeedback) {
  const props = reactive({ goalId: 'g1', itemId: 'i1', attempt: 1, status, cue, scanning: true });
  const root = document.createElement('div'); document.body.append(root);
  const app = createApp(() => h(BatchFeedbackIcon, props)); app.mount(root);
  dispose = () => { app.unmount(); root.remove(); };
  return { props, root };
}
const feedback = (kind: BatchFeedback['kind'] = 'verified'): BatchFeedback => ({ key: kind, goalId: 'g1', itemId: 'i1', attempt: 1, kind, at: Date.now(), seq: 11 });
it('claims only when visible and pauses the same SVG across visibility changes', async () => {
  const { root } = mount('SUCCEEDED', feedback());
  expect(claim).not.toHaveBeenCalled();
  intersect([{ isIntersecting: true }]); await nextTick();
  expect(claim).toHaveBeenCalledTimes(1);
  const svg = root.querySelector('svg');
  await vi.advanceTimersByTimeAsync(100);
  intersect([{ isIntersecting: false }]); await nextTick();
  await vi.advanceTimersByTimeAsync(2000);
  expect(root.querySelector('svg')).toBe(svg);
  expect(root.querySelector('.is-verified-feedback')).not.toBeNull();
  intersect([{ isIntersecting: true }]); await nextTick();
  expect(root.querySelector('svg')).toBe(svg);
  expect(claim).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(850);
  expect(root.querySelector('.is-verified-feedback')).toBeNull();
});
it('rejects old attempts and failure statuses, and renders quiet icons without question marks', async () => {
  const { props, root } = mount('DETECTING', feedback('receipt'));
  props.attempt = 2; intersect([{ isIntersecting: true }]); await nextTick();
  expect(claim).not.toHaveBeenCalled();
  props.status = 'FAILED'; await nextTick();
  expect(root.querySelector('.feedback-error')?.textContent).toBe('!');
  expect(root.querySelector('.is-verified-feedback')).toBeNull();
  props.status = 'WAITING_SR'; await nextTick(); expect(root.querySelector('.feedback-quiet path')?.getAttribute('d')).toContain('l3');
  props.status = 'NO_ISSUE'; await nextTick(); expect(root.querySelector('.feedback-quiet path')?.getAttribute('d')).toBe('M8 12h8');
  expect(root.textContent).not.toContain('?');
});
it('does not replay a claimed cue after component remount', async () => {
  const cue = feedback(); mount('SUCCEEDED', cue);
  intersect([{ isIntersecting: true }]); await nextTick(); dispose!();
  const { root } = mount('SUCCEEDED', cue);
  intersect([{ isIntersecting: true }]); await nextTick();
  expect(root.querySelector('.is-verified-feedback')).toBeNull();
});
