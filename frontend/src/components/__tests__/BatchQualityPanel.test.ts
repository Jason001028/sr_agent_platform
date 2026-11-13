// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createApp, nextTick, reactive } from 'vue';
import type { App } from 'vue';

const state = vi.hoisted(() => ({ quality: null as any }));
vi.mock('../../stores/batchQuality', () => ({
  useBatchQualityStore: () => state.quality,
}));

import BatchQualityPanel from '../BatchQualityPanel.vue';
import { batchQualityRequest } from '../../lib/batchDock';

let app: App | undefined;
let root: HTMLDivElement;
async function flush() {
  for (let i = 0; i < 20; i++) { await Promise.resolve(); await nextTick(); }
}

beforeEach(() => {
  const doc = {
    review_id: 'review-i2', goal_id: 'g', item_id: 'i2', job_id: '82',
    version: 0, scene_decision: 'pending',
    regions: [
      { region_id: 'r1', decision: 'pending', reason: '', reviewer: '' },
      { region_id: 'r2', decision: 'pending', reason: '', reviewer: '' },
    ],
    images: [], scene_path: '/scene/i2',
    binding: {
      mask: { width: 128, height: 128, sha256: 'sha' },
      output: { image_key: 'o' }, nosr: { image_key: 'n' },
      regions: [
        { candidate_id: 'r1', kind: 'bbox', bbox: [20, 20, 40, 40], points: null },
        { candidate_id: 'r2', kind: 'bbox', bbox: [70, 70, 90, 90], points: null },
      ],
    },
    audit: [],
  };
  state.quality = reactive({
    reviews: [{
      review_id: 'review-i2', item_id: 'i2', name: 'B', scene_name: 'SCENE_B',
      job_id: '82', status: 'AWAITING_REVIEW', scene_decision: 'pending',
      version: 0, current: true, reasons: [],
      counts: { approved: 0, rejected: 0, pending: 2 },
    }],
    doc: null, regionId: '', error: '', busy: false, loading: false,
    reviewer: '', reason: '', overlay: 'fill', pair: null,
    selectedRegion: null, comparisonReady: false,
    totals: { scenes: 1, regions: 2, pending: 2, approved: 0, rejected: 0 },
    refresh: vi.fn(async () => {}),
    select: vi.fn(async () => {
      state.quality.doc = doc;
      state.quality.regionId = 'r1';
      state.quality.selectedRegion = doc.binding.regions[0];
    }),
    openRegion: vi.fn(async () => {}),
    flicker: vi.fn(),
    decide: vi.fn(async () => {}),
  });
  batchQualityRequest.value = { token: 1, itemId: 'i2' };
  root = document.createElement('div');
  document.body.append(root);
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  root.remove();
  batchQualityRequest.value = null;
});

it('lands directly on the requested review item and clears the one-shot intent', async () => {
  app = createApp(BatchQualityPanel);
  app.mount(root);
  await flush();
  expect(state.quality.select).toHaveBeenCalledWith('i2');
  expect(batchQualityRequest.value).toBeNull();
  expect(root.querySelector('[data-e2e=batch-quality-panel]')).not.toBeNull();
  expect(root.querySelectorAll('[data-e2e=quality-region-chips] button')).toHaveLength(2);
  expect(root.textContent).toContain('区域 1 · 待复核');
  expect(root.textContent).toContain('Job 82');
});
