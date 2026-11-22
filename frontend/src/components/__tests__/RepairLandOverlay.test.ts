// @vitest-environment jsdom
import { createApp, nextTick, reactive } from 'vue';
import { expect, it, vi } from 'vitest';
import Overlay from '../RepairLandOverlay.vue';

const source = { sceneId: 'pan', route: 'jpg', srcRev: 'r1', W: 8, H: 4, thumb: { width: 4, height: 2 } };
const rect = { x: 21, y: 5, w: 50, h: 40 };
const view = { ox: 3, oy: 4, scale: 2 };
const viewer = reactive({
  overlayMasterVisible: true, drawMode: false,
  repairLandTarget: { scene_id: 'pan', preview_revision: 'r1', width: 8, height: 4,
    preview_url: '/api/repairs/p/land-preview/i?session_id=s' },
  panes: [
    { rec: { ...source }, rect, view, side: 'A' },
    { rec: { ...source, srcRev: 'r2' }, rect, view, side: 'B' },
  ],
});
vi.mock('../../stores/viewer', () => ({ useViewerStore: () => viewer }));

it('only overlays the matching PAN pane and hides imagery on stale or failed previews', async () => {
  const root = document.createElement('div');
  const app = createApp(Overlay);
  app.mount(root);
  try {
    expect(root.querySelectorAll('img')).toHaveLength(1);
    expect(root.querySelector('img')?.style.left).toBe('3px');
    expect(root.querySelector('img')?.getAttribute('src')).toBe(viewer.repairLandTarget.preview_url);
    root.querySelector('img')?.dispatchEvent(new Event('error'));
    await nextTick();
    expect(root.querySelector('img')).toBeNull();
    expect(root.textContent).toContain('陆地约束预览不可用');
    root.querySelector('button')?.click();
    await nextTick();
    expect(root.querySelector('img')).not.toBeNull();
    viewer.overlayMasterVisible = false;
    await nextTick();
    expect(root.querySelector('img')).toBeNull();
    viewer.overlayMasterVisible = true;
    viewer.panes[0].rec.srcRev = 'r3';
    await nextTick();
    expect(root.querySelector('img')).toBeNull();
    expect(root.textContent).toContain('PAN 版本不匹配');
  } finally {
    app.unmount();
  }
});
