// @vitest-environment jsdom
import { createPinia, setActivePinia } from 'pinia';
import { expect, it, vi } from 'vitest';
vi.mock('../../lib/api.js', () => ({ apiListQueue: vi.fn(), apiSubmitQueue: vi.fn(), apiCancelQueue: vi.fn(), subscribeQueueEvents: vi.fn() }));
import { apiListQueue } from '../../lib/api';
import type { QueueTask } from '../../lib/api';
import { useQueueStore } from '../queue';
it('serializes concurrent list requests and publishes only the trailing refresh', async () => {
  setActivePinia(createPinia());
  let resolveOld!: (rows: QueueTask[]) => void;
  let resolveNew!: (rows: QueueTask[]) => void;
  vi.mocked(apiListQueue).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }))
    .mockImplementationOnce(() => new Promise(resolve => { resolveNew = resolve; }));
  const queue = useQueueStore(); const first = queue.list();
  const others = Array.from({ length: 50 }, () => queue.list());
  expect(apiListQueue).toHaveBeenCalledTimes(1);
  resolveOld([{ task_id: 1 } as QueueTask]); await Promise.resolve(); await Promise.resolve();
  expect(queue.tasks).toEqual([]); expect(apiListQueue).toHaveBeenCalledTimes(2);
  resolveNew([{ task_id: 2 } as QueueTask]); await Promise.all([first, ...others]);
  expect(queue.tasks.map(t => t.task_id)).toEqual([2]); expect(queue.loading).toBe(false);
});
