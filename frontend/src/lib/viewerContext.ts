import { buildViewerContextSnapshot } from './agentContext.js';
﻿import type { ViewerRec } from '../stores/viewer.js';

/** Copy only bounded metadata; never serialize File, canvas or pixel buffers. */
export function snapshotViewer(viewer: {
  activeRec: ViewerRec | null;
  selectedIds: number[];
  panes: { side: string; rec: ViewerRec | null }[];
}, attachmentCount: number) {
  const info = (r: ViewerRec | null) => r ? {
    // Reuse the existing metadata projection; pixel buffers stay in Viewer.
    ...buildViewerContextSnapshot({ name: r.name, sceneId: r.sceneId, W: r.W, H: r.H,
      dispW: r.thumb?.width ?? 0, dispH: r.thumb?.height ?? 0, stretch: r.paintedMode ?? '', roi: null }).image,
    id: r.id, name: r.name, scene_id: r.sceneId, source: r.route,
    width: r.W || null, height: r.H || null, bands: r.nbands || null,
    size_bytes: r.size || null, layout: r.layout,
    bits: r.probe?.bits ?? null, sample_format: r.probe?.sampleFormat ?? null,
    compression: r.probe?.compression ?? null, metadata_source: 'viewer', pixels_loaded: Boolean(r.thumb),
  } : null;
  return {
    version: 1, captured_at: new Date().toISOString(),
    current: info(viewer.activeRec), selected_ids: [...viewer.selectedIds].slice(0, 100),
    displayed: viewer.panes.map(p => ({ side: p.side, image: info(p.rec?.thumb ? p.rec : null) })),
    attachment_count: attachmentCount,
  };
}

let capture: ((count: number) => ReturnType<typeof snapshotViewer>) | undefined;
export function registerViewerContext(provider: NonNullable<typeof capture>) { capture = provider; }
export function captureViewerContext(count: number) {
  return capture?.(count) ?? { version: 1, current: null, displayed: [], selected_ids: [], attachment_count: count };
}
