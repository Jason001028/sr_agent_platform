/**
 * viewer/e2eHooks.ts — window.__viewer 全局测试钩子（tif-viewer.html 直译）
 * ------------------------------------------------------------------
 * 浏览器回归 + 真机验收都靠它，**始终暴露**（不只在 dev/build 注入）。
 * HTML 版 `window.__viewer`（1275-1285 行）的行为对齐：
 *   - 解码/导出：planExport / bakeJpg(canvasToJpegBlob) / getSaver / fsIO / setSaver /
 *     collectForExport / kickExport / reExportJpg / scanPendingExports / dateDirName /
 *     JPG_MAX / JPG_QUALITY
 *   - 掩码：enterDraw / exitDraw / buildMaskJson / exportMaskJson / thumbToOrig /
 *     getRois / genMask / wandSelect / maskGen(MaskGen)
 *   - 新增（Vue 无 HTML 的全局 recs 数组，测试观测需要）：recs() / activeRec() 摘要快照。
 *   - setSparseMin：改写 tifDecode 的 SPARSE_MIN（默认 1e8 不动），让浏览器回归用小
 *     fixture 走稀疏路由。
 *
 * 全部委托 store 动作 + lib 纯函数；store 的 ViewerRec 满足 ExportRecLike（file/probe/exportCap）。
 */
import { browserKit } from '../lib/browserKit.js';
import { planExport, JPG_MAX, JPG_QUALITY, setSparseMin } from '../lib/tifDecode.js';
import type { ProbeInfo } from '../lib/tifDecode.js';
import { collectForExport, canvasToJpegBlob } from '../lib/exportJpg.js';
import type { ExportCollectResult } from '../lib/exportJpg.js';
import { getSaver, fsIO, setSaverOverride, dateDirName } from '../lib/saver.js';
import type { Saver } from '../lib/saver.js';
import { thumbToOrig } from '../lib/viewMath.js';
import MaskGen from '../lib/maskgen.js';
import { FileSource } from '../lib/source.js';
import { useViewerStore } from '../stores/viewer.js';
import type { ViewerRec } from '../stores/viewer.js';

/** rec 摘要快照（浏览器测试观测用；HTML 直接读全局 recs 的等价物） */
export interface ViewerRecSummary {
  id: number;
  name: string;
  size: number;
  status: string;
  W: number;
  H: number;
  route: ViewerRec['route'];
  thumbW: number;
  thumbH: number;
  layout: string;
  jpgStatus: string;
  jpgCls: ViewerRec['jpgCls'];
  jpgDone: boolean;
  /** 拉伸后的缩略图画布（页内采样用；等价 HTML rec.thumb） */
  thumb: HTMLCanvasElement | null;
}

export interface ViewerHook {
  // 解码 / 导出
  planExport: typeof planExport;
  bakeJpg: (rgba: Uint8ClampedArray, sw: number, sh: number) => Promise<Blob>;
  getSaver: typeof getSaver;
  fsIO: typeof fsIO;
  setSaver: (s: Saver | null) => void;
  collectForExport: (
    rec: { file: File; probe: ProbeInfo | null; _exportCap?: number },
    onProgress?: (f: number) => void,
  ) => Promise<ExportCollectResult>;
  kickExport: (rec: ViewerRec, force?: boolean) => void;
  reExportJpg: (id: number) => void;
  scanPendingExports: () => void;
  dateDirName: typeof dateDirName;
  JPG_MAX: number;
  JPG_QUALITY: number;
  // 掩码
  enterDraw: () => void;
  exitDraw: () => void;
  buildMaskJson: () => ReturnType<ReturnType<typeof useViewerStore>['buildMaskJson']>;
  exportMaskJson: () => void;
  thumbToOrig: typeof thumbToOrig;
  getRois: () => ReturnType<ReturnType<typeof useViewerStore>['getRois']>;
  genMask: () => Promise<void>;
  wandSelect: (sx: number, sy: number) => void;
  maskGen: typeof MaskGen;
  setSparseMin: (n: number) => void;
  // 掩码直接驱动（等效画布点击路径，浏览器回归用；HTML 钩子没有，因 HTML 无对应测试）
  setDrawTool: (t: import('../stores/viewer.js').DrawTool) => void;
  commitRect: (r: { x0: number; y0: number; x1: number; y1: number }) => void;
  undoRoi: () => void;
  clearRois: () => void;
  mergeRois: () => Promise<void>;
  delClick: (tx: number, ty: number) => void;
  // 测试观测（Vue 无全局 recs → 摘要快照）
  recs: () => ViewerRecSummary[];
  activeRec: () => ViewerRecSummary | null;
}

declare global {
  interface Window {
    __viewer?: ViewerHook;
  }
}

function summarize(rec: ViewerRec): ViewerRecSummary {
  return {
    id: rec.id,
    name: rec.name,
    size: rec.size,
    status: rec.status,
    W: rec.W,
    H: rec.H,
    route: rec.route,
    thumbW: rec.thumb ? rec.thumb.width : 0,
    thumbH: rec.thumb ? rec.thumb.height : 0,
    layout: rec.layout,
    jpgStatus: rec.jpgStatus,
    jpgCls: rec.jpgCls,
    jpgDone: rec._jpgDone,
    thumb: (rec.thumb as unknown as HTMLCanvasElement | null),
  };
}

/** 构建并挂到 window.__viewer（幂等；始终暴露给 e2e 与真机验收） */
export function mountE2EHooks(): ViewerHook {
  const hook: ViewerHook = {
    planExport,
    bakeJpg: (rgba, sw, sh) => canvasToJpegBlob(rgba, sw, sh, browserKit),
    getSaver,
    fsIO,
    setSaver: (s) => setSaverOverride(s),
    collectForExport: (rec, onProgress) => {
      const store = useViewerStore();
      const src = new FileSource(rec.file, rec.file.name);
      return collectForExport(
        { file: rec.file, probe: rec.probe, exportCap: rec._exportCap },
        src,
        browserKit,
        onProgress,
      );
    },
    kickExport: (rec, force) => useViewerStore().kickExport(rec, force),
    reExportJpg: (id) => useViewerStore().reExportJpg(id),
    scanPendingExports: () => useViewerStore().scanPendingExports(),
    dateDirName,
    JPG_MAX,
    JPG_QUALITY,
    enterDraw: () => useViewerStore().enterDraw(),
    exitDraw: () => useViewerStore().exitDraw(),
    buildMaskJson: () => useViewerStore().buildMaskJson(),
    exportMaskJson: () => useViewerStore().exportMaskJson(),
    thumbToOrig,
    getRois: () => useViewerStore().getRois(),
    genMask: () => useViewerStore().genMask(),
    wandSelect: (sx, sy) => useViewerStore().wandSelect(sx, sy),
    maskGen: MaskGen,
    setSparseMin,
    setDrawTool: (t) => useViewerStore().setDrawTool(t),
    commitRect: (r) => {
      const store = useViewerStore();
      store.pendingRect = { x0: r.x0, y0: r.y0, x1: r.x1, y1: r.y1 };
      store.commitRect();
    },
    undoRoi: () => useViewerStore().undoRoi(),
    clearRois: () => useViewerStore().clearRois(),
    mergeRois: () => useViewerStore().mergeRois(),
    delClick: (tx, ty) => useViewerStore().delClick(tx, ty),
    recs: () => useViewerStore().recs.map(summarize),
    activeRec: () => {
      const rec = useViewerStore().activeRec;
      return rec ? summarize(rec) : null;
    },
  };
  if (window.__viewer !== hook) window.__viewer = hook;
  return hook;
}
