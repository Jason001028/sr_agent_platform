// @vitest-environment jsdom
import { createApp, nextTick } from 'vue';
import { expect, it, vi } from 'vitest';
import SRSubmitFeedback from '../SRSubmitFeedback.vue';
import type { QueueSubmitResult } from '../../lib/api';

it('locks the toolbar button until the real receipt and compact transition are ready', async () => {
  vi.useFakeTimers();
  let finish!: (receipt: QueueSubmitResult) => void;
  const submit = vi.fn(() => new Promise<QueueSubmitResult>(resolve => { finish = resolve; }));
  const accepted = vi.fn();
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp(SRSubmitFeedback, { compact: true, submit, onAccepted: accepted });
  app.mount(root);
  try {
    const button = root.querySelector<HTMLButtonElement>('.sr-submit')!;
    button.click();
    button.click();
    expect(submit).toHaveBeenCalledOnce();
    await nextTick();
    expect(button.disabled).toBe(true);
    expect(button.getAttribute('aria-busy')).toBe('true');
    expect(button.querySelector('.sr-spinner')).not.toBeNull();
    finish({ task_id: 42, job_id: 52, status: 'SUBMITTED', state: 'PENDING', config_xml: null, log_dir: null });
    await vi.advanceTimersByTimeAsync(239);
    expect(button.classList.contains('submitting')).toBe(true);
    expect(accepted).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(button.classList.contains('accepted')).toBe(true);
    expect(button.textContent).toContain('已提交');
    expect(button.getAttribute('aria-busy')).toBe('false');
    expect(accepted).toHaveBeenCalledWith(42, true);
  } finally {
    app.unmount();
    root.remove();
    vi.useRealTimers();
  }
});
