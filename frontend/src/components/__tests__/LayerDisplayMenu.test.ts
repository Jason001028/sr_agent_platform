// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createApp, nextTick, reactive } from 'vue';
import type { App } from 'vue';
const state = vi.hoisted(() => ({ viewer: {} as any, diff: {} as any }));
vi.mock('../../stores/viewer', () => ({ useViewerStore: () => state.viewer }));
vi.mock('../../stores/diff', () => ({ useDiffStore: () => state.diff }));
import Panel from '../LayerDisplayMenu.vue';
let app: App, root: HTMLDivElement;
beforeEach(() => {
  state.viewer = reactive({ overlayMasterVisible: true, repairOverlaySelected: true,
    roiOverlaySelected: true, agentAnnotationsVisible: false,
    setAgentAnnotationsVisible(value: boolean) { this.agentAnnotationsVisible = value; } });
  state.diff = reactive({ resultVisible: true });
  root = document.createElement('div'); document.body.append(root);
  app = createApp(Panel); app.mount(root);
});
afterEach(() => { app.unmount(); root.remove(); });
it('renders four layer toggles and changes only the selected layer', async () => {
  expect(root.querySelectorAll('.layer-row')).toHaveLength(4);
  for (const key of ['difference', 'repair', 'annotations', 'roi']) {
    root.querySelector<HTMLButtonElement>(`[data-layer=${key}]`)!.click();
  }
  await nextTick();
  expect(state.diff.resultVisible).toBe(false);
  expect(state.viewer.repairOverlaySelected).toBe(false);
  expect(state.viewer.agentAnnotationsVisible).toBe(true);
  expect(state.viewer.roiOverlaySelected).toBe(false);
  expect(state.viewer.overlayMasterVisible).toBe(true);
});
it('temporarily hides and restores all layers without changing selections', async () => {
  const button = root.querySelector<HTMLButtonElement>('footer button')!;
  button.click(); await nextTick();
  expect(state.viewer.overlayMasterVisible).toBe(false);
  expect(button.textContent).toContain('恢复显示');
  expect(state.diff.resultVisible).toBe(true);
  expect(state.viewer.repairOverlaySelected).toBe(true);
  expect(state.viewer.agentAnnotationsVisible).toBe(false);
  expect(state.viewer.roiOverlaySelected).toBe(true);
  button.click(); await nextTick();
  expect(state.viewer.overlayMasterVisible).toBe(true);
  expect(button.textContent).toContain('全部隐藏');
});
