// @vitest-environment jsdom
import { expect, it, vi } from 'vitest';
import { createApp, nextTick, reactive } from 'vue';
import type { QueueTask } from '../../lib/api';

const state = vi.hoisted(() => ({ queue: undefined as unknown }));
vi.mock('vue-router', () => ({ useRoute: () => ({ hash: '' }) }));
vi.mock('../../stores/queue.js', async (original) => ({
  ...await original<typeof import('../../stores/queue')>(),
  useQueueStore: () => state.queue,
}));
import QueuePage from '../../pages/QueuePage.vue';

it('toggles the failure details below the status without showing details from a prior attempt', async () => {
  const reason = 'Mask dimensions match neither body nor NOSR';
  const task = { task_id: 1, job_id: 42, state: 'FAILED', fingerprint: 'fp', failure_reason: reason,
    params: { lq_path: '/DiskArray/scene', sr_scale: 2 }, created_at: 0 } as QueueTask;
  const queue = reactive({ tasks: [task], list: vi.fn(), connect: vi.fn(), disconnect: vi.fn() });
  state.queue = queue;
  const root = document.createElement('div');
  const app = createApp(QueuePage);
  app.mount(root);
  try {
    const label = root.querySelector<HTMLButtonElement>('.failure-tag')!;
    expect(label.getAttribute('aria-expanded')).toBe('false');
    label.click(); await nextTick();
    expect(root.querySelector('.failure-panel')?.textContent).toContain(reason);
    label.click(); await nextTick();
    expect(root.querySelector('.failure-panel')).toBeNull();
    label.click(); await nextTick();
    queue.tasks = [{ ...task, job_id: 43, failure_reason: 'another attempt' }];
    await nextTick();
    expect(root.querySelector('.failure-panel')).toBeNull();
  } finally {
    app.unmount();
  }
});
