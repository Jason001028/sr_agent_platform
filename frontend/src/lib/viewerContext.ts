import { buildViewerContextSnapshot } from './agentContext.js';
import type { ViewerRec } from '../stores/viewer.js';
import { imageRef } from './spatial';

/** 工作区候选集的条数上限（后端同名上限见 services/viewer_context.py）。 */
const WORKSPACE_MAX_ITEMS = 200;

export interface WorkspaceItem {
  /** 不透明场景 id（与 /api/scenes 行的 id 同源）；后端会再过一遍白名单。 */
  scene_id: string;
  name: string;
}

export interface WorkspaceSnapshot {
  resources?: {id:number;name:string;image_key:string|null;source_revision:string|null;asset_id:string|null;width:number;height:number}[];
  /** 左侧临时工作区版本；清空/刷新后自增，旧快照据此失效。 */
  version: number;
  /** 有盘阵场景身份的影像（本地拖入的图不在其中）。 */
  items: WorkspaceItem[];
  /** 去重后的场景身份总数（items 被截断时 > items.length）。 */
  total: number;
  /** 没有场景身份、无法参与盘阵检索的影像数。 */
  local_only: number;
  /** 仅指场景候选 items 被丢掉；修复计划据此拒绝静默规划前缀。 */
  truncated: boolean;
  /** 仅指本地资源身份被裁掉；不影响场景候选的规划。 */
  resources_truncated?: boolean;
}

/** 左侧列表 → 工作区候选集。纯函数：只复制 id 与名字，不序列化任何图像数据。 */
export function snapshotWorkspace(
  recs: ViewerRec[], version: number,
): WorkspaceSnapshot {
  const seen = new Set<string>();
  const items: WorkspaceItem[] = [];
  const resources: NonNullable<WorkspaceSnapshot['resources']> = [];
  let total = 0;
  let localOnly = 0;
  for (const r of recs) {
    if (!r.sceneId) {
      localOnly += 1;
      if (resources.length < WORKSPACE_MAX_ITEMS) resources.push({id:r.id,name:r.name.slice(0,512),
        image_key:r.file?imageRef(r)?.image_key??null:null,source_revision:r.srcRev??null,
        asset_id:r.previewAsset?.id??null,width:r.W||0,height:r.H||0});
      continue;
    }
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
    resources,
    total,
    local_only: localOnly,
    truncated: total > items.length,
    resources_truncated: localOnly > resources.length,
  };
}

/** Copy only bounded metadata; never serialize File, canvas or pixel buffers. */
export function snapshotViewer(viewer: {
  activeRec: ViewerRec | null;
  selectedIds: number[];
  panes: { side: string; rec: ViewerRec | null }[];
  recs?: ViewerRec[];
  workspaceVersion?: number;
  selRoi?: number;
  drawMode?: boolean;
  drawTool?: string;
  pendingPts?: number[][] | null;
  pendingRect?: {x0:number;y0:number;x1:number;y1:number} | null;
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
    image_key: r.spatialIdentity && r.spatialIdentityToken===r.token ? r.spatialIdentity
      : r.file ? imageRef(r)?.image_key ?? null : null,
    source_revision: r.srcRev ?? null, stage: r.stageKind ?? null,
    asset_id: r.previewAsset?.id ?? null,
  } : null;
  const active = viewer.activeRec;
  const polygon = active?.maskRois?.[viewer.selRoi ?? -1];
  const sourcePoint = (p: number[]) => [p[0] * active!.W / active!.thumb!.width, p[1] * active!.H / active!.thumb!.height];
  const points = active?.thumb && polygon ? polygon.slice(0,128).map(sourcePoint) : [];
  const roi = polygon && active?.thumb ? {
    image_id: active.id, index: viewer.selRoi, coordinate_space: 'source_pixels',
    bbox: [Math.min(...polygon.map(p=>p[0]))*active.W/active.thumb.width,
      Math.min(...polygon.map(p=>p[1]))*active.H/active.thumb.height,
      Math.max(...polygon.map(p=>p[0]))*active.W/active.thumb.width,
      Math.max(...polygon.map(p=>p[1]))*active.H/active.thumb.height],
    points, vertex_count: polygon.length, truncated: polygon.length > points.length,
  } : null;
  const selected = (viewer.recs ?? []).filter(r=>viewer.selectedIds.includes(r.id));
  return {
    version: 1, captured_at: new Date().toISOString(),
    current: info(viewer.activeRec), selected_ids: [...viewer.selectedIds].slice(0, 100),
    displayed: viewer.panes.map(p => ({ side: p.side, image: info(p.rec?.thumb ? p.rec : null) })),
    workspace: snapshotWorkspace(viewer.recs ?? [], viewer.workspaceVersion ?? 0),
    attachment_count: attachmentCount,
    selected_images: selected.slice(0,24).map(r=>({id:r.id,name:r.name.slice(0,512),scene_id:r.sceneId??null,
      image_key:r.spatialIdentity&&r.spatialIdentityToken===r.token?r.spatialIdentity:r.file?imageRef(r)?.image_key??null:null,source_revision:r.srcRev??null,asset_id:r.previewAsset?.id??null,width:r.W,height:r.H})),
    selected_total: viewer.selectedIds.length, selected_truncated: selected.length > 24,
    roi,
    masks: { image_id: active?.id ?? null, count: active?.maskRois?.length ?? 0,
      editing: Boolean(viewer.drawMode), tool: viewer.drawTool ?? null,
      draft_vertex_count: viewer.pendingPts?.length ?? 0, rectangle_in_progress: Boolean(viewer.pendingRect),
      draft_coordinate_space: 'source_pixels',
      draft_points: active?.thumb ? (viewer.pendingPts ?? []).slice(0,128).map(sourcePoint) : [],
      draft_truncated: (viewer.pendingPts?.length ?? 0)>128 },
  };
}

let capture: ((count: number) => ReturnType<typeof snapshotViewer>) | undefined;
const observations = new Map<string, () => unknown>();
export function registerViewerObservation(name: 'differences' | 'batch_goal' | 'batch_draft' | 'repair_selection' | 'qc_draft' | 'repair_item', provider: () => unknown) {
  observations.set(name, provider);
  return () => { if (observations.get(name) === provider) observations.delete(name); };
}
export function registerViewerContext(provider: NonNullable<typeof capture>) { capture = provider; }
export function captureViewerContext(count: number) {
  const snapshot = capture?.(count) ?? { version: 1, current: null, displayed: [], selected_ids: [],
    workspace: { version: 0, items: [], total: 0, local_only: 0, truncated: false },
    attachment_count: count };
  // Freeze at send time, including provider-owned arrays; later UI edits cannot
  // mutate the request while attachments/session setup are awaiting IO.
  return JSON.parse(JSON.stringify({ ...snapshot,
    ...Object.fromEntries([...observations].map(([name, provider])=>[name, provider()])) })) as
    typeof snapshot & Record<string, unknown>;
}
