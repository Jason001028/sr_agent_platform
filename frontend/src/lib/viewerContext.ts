import { buildViewerContextSnapshot } from './agentContext.js';
import type { ViewerRec } from '../stores/viewer.js';

/** 工作区候选集的条数上限（后端同名上限见 services/viewer_context.py）。 */
const WORKSPACE_MAX_ITEMS = 200;

export interface WorkspaceItem {
  /** 不透明场景 id（与 /api/scenes 行的 id 同源）；后端会再过一遍白名单。 */
  scene_id: string;
  name: string;
}

export interface WorkspaceSnapshot {
  /** 左侧临时工作区版本；清空/刷新后自增，旧快照据此失效。 */
  version: number;
  /** 有盘阵场景身份的影像（本地拖入的图不在其中）。 */
  items: WorkspaceItem[];
  /** 去重后的场景身份总数（items 被截断时 > items.length）。 */
  total: number;
  /** 没有场景身份、无法参与盘阵检索的影像数。 */
  local_only: number;
  truncated: boolean;
}

/** 左侧列表 → 工作区候选集。纯函数：只复制 id 与名字，不序列化任何图像数据。 */
export function snapshotWorkspace(
  recs: ViewerRec[], version: number,
): WorkspaceSnapshot {
  const seen = new Set<string>();
  const items: WorkspaceItem[] = [];
  let total = 0;
  let localOnly = 0;
  for (const r of recs) {
    if (!r.sceneId) { localOnly += 1; continue; }
    // 同一景可能被打开两次（输入 / 产物各一行）：按场景 id 去重，total 也只
    // 数一景 —— truncated 的判据是「有条目被丢掉了没有」，不能因为重复而误报。
    if (seen.has(r.sceneId)) continue;
    seen.add(r.sceneId);
    total += 1;
    if (items.length < WORKSPACE_MAX_ITEMS) {
      items.push({ scene_id: r.sceneId, name: r.name });
    }
  }
  return {
    version,
    items,
    total,
    local_only: localOnly,
    truncated: total > items.length,
  };
}

/** Copy only bounded metadata; never serialize File, canvas or pixel buffers. */
export function snapshotViewer(viewer: {
  activeRec: ViewerRec | null;
  selectedIds: number[];
  panes: { side: string; rec: ViewerRec | null }[];
  recs?: ViewerRec[];
  workspaceVersion?: number;
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
    workspace: snapshotWorkspace(viewer.recs ?? [], viewer.workspaceVersion ?? 0),
    attachment_count: attachmentCount,
  };
}

let capture: ((count: number) => ReturnType<typeof snapshotViewer>) | undefined;
export function registerViewerContext(provider: NonNullable<typeof capture>) { capture = provider; }
export function captureViewerContext(count: number) {
  return capture?.(count) ?? { version: 1, current: null, displayed: [], selected_ids: [],
    workspace: { version: 0, items: [], total: 0, local_only: 0, truncated: false },
    attachment_count: count };
}
