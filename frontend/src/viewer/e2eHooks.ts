/**
 * viewer/e2eHooks.ts — window.__viewer 全局测试钩子（tif-viewer.html 直译）
 * ------------------------------------------------------------------
 * 浏览器回归 + 真机验收都靠它，**始终暴露**（不只在 dev/build 注入）。
 * HTML 版 `window.__viewer`（1275-1285 行）的行为对齐：
 *   - 解码：planExport
 *   - 掩码：enterDraw / exitDraw / buildMaskJson / exportMaskJson / thumbToOrig /
 *     getRois / genMask / wandSelect / maskGen(MaskGen)
 *   - 新增（Vue 无 HTML 的全局 recs 数组，测试观测需要）：recs() / activeRec() 摘要快照。
 *   - 待修复清单（HTML 版没有这个功能）：qcImport / qcClose / qcSetStatus / qcOutput /
 *     qcState / qcOpenByName。
 *   - setSparseMin：改写 tifDecode 的 SPARSE_MIN（默认 1e8 不动），让浏览器回归用小
 *     fixture 走稀疏路由。
 *   - 删去（最小原型取消了浏览器 JPG 导出/输出目录一条链路）：bakeJpg / getSaver /
 *     fsIO / setSaver / collectForExport / kickExport / reExportJpg /
 *     scanPendingExports / dateDirName / JPG_MAX / JPG_QUALITY。
 *
 * 全部委托 store 动作 + lib 纯函数。
 */
import { browserKit } from '../lib/browserKit.js';
import { planExport, setSparseMin } from '../lib/tifDecode.js';
import type { StretchMode } from '../lib/tifDecode.js';
import { thumbToOrig } from '../lib/viewMath.js';
import MaskGen from '../lib/maskgen.js';
import type { SceneOpenMeta } from '../lib/scene.js';
import type { StageKind } from '../lib/stage.js';
import { useViewerStore } from '../stores/viewer.js';
import type { ViewerRec } from '../stores/viewer.js';
import { useQcListStore } from '../stores/qclist.js';
import { useScenesStore } from '../stores/scenes.js';
import { useNoticesStore } from '../stores/notices.js';
import { useQueueStore } from '../stores/queue.js';
import type { QcStatus } from '../lib/qclist.js';

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
  /** 盘阵目录（提交 SR 的判据；反推关联成功后才非空） */
  lqPath: string | null;
  /** 掩码写进服务端后的路径（保存掩码到盘阵成功才非空） */
  serverMaskPath: string | null;
  /** 拉伸后的缩略图画布（页内采样用；等价 HTML rec.thumb） */
  thumb: HTMLCanvasElement | null;
  /** 这张图**实际**画出来的拉伸模式（null = 还没画过）。 */
  paintedMode: StretchMode | null;
  /** 不透明场景 id（route='jpg' 才有；升级/打开盘阵场景后非空）。 */
  sceneId: string | null;
  /** 场景目录（卡片上「同一景共用一个序号」那颗小标的分组键，与 `lqPath` 正交：
   *  中间产物的 `lqPath` 是空的、这一项照给）。库外单张图为 null。 */
  sceneDir: string | null;
  /** 环节与它的标签（卡片上「本体 / SR / NOSR」那颗小标的数据源）。 */
  stageKind: StageKind | null;
  stageLabel: string | null;
  /** 反推关联**失败**时服务端给的原因（成功或没试过则 undefined）。
   *  单独暴露：rec.status 会被解码进度覆盖，失败原因在别处看不到。 */
  linkNote?: string;
  /** 缩放预热是否已经做过这份显示画布（见 stores/viewer.warmZoom）。
   *  探针/回归要能分辨「预热没做」与「预热做了但不灵」。 */
  warmed: boolean;
}

export interface ViewerHook {
  // 解码
  planExport: typeof planExport;
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
  // 阶段5：盘阵场景打开（route='jpg'，浏览器回归注入合成 JPG）+ 掩码→SR 提交
  openSceneJpg: (meta: SceneOpenMeta, blob: Blob) => Promise<void>;
  // 本地 .jpg/.jpeg 打开（route='img'，浏览器回归用小 JPG fixture 走同一像素管线）
  openLocalImage: (file: File) => Promise<void>;
  submitSr: () => void;
  // 盘阵任意场景目录（手工路径）：打开 / 本地文件反推关联 / 掩码写进服务端
  openScenePath: (path: string) => Promise<boolean>;
  /** 反推关联当前图；**返回是否命中并已升级成盘阵 JPG**（命中即不做本地解码）。 */
  tryLinkScenes: () => Promise<boolean>;
  bakeMaskToServer: () => Promise<boolean>;
  /** 模态提示当前内容（没弹则 visible=false）。全局单例，与具体 rec 无关。 */
  modal: () => ReturnType<typeof useViewerStore>['modal'];
  hideModal: () => void;
  /** 解码/预热遮罩是否还盖着。**状态就绪 ≠ 可以发真实输入**：遮罩盖着画布时
      `page.mouse.wheel` 会被它吃掉（命中测试走它，合成 dispatchEvent 才绕得过），
      于是「手势零效果」会被误读成「渲染没干活」。发真实鼠标事件前先等它收起。 */
  overlayVisible: () => boolean;
  // 测试观测（Vue 无全局 recs → 摘要快照）
  recs: () => ViewerRecSummary[];
  activeRec: () => ViewerRecSummary | null;
  /** 当前图实际的拉伸模式（工具栏下拉显示的那个值）。 */
  activeStretch: () => StretchMode;
  /** 直接切拉伸（等效工具栏下拉 change）。 */
  setStretch: (m: StretchMode) => void;
  /** 预览烘焙档位（各边 ÷N，工具栏拖动条那个值）。 */
  previewDiv: () => number;
  /** 直接切档位（等效工具栏拖动条 input）。 */
  setPreviewDiv: (div: number) => void;
  // 待修复清单（ROI/工具 置顶面板）：导入 / 标记 / 生成写回文本 / 真的写回。
  // 写盘改走后端之后这步进得了回归了（原先走 FSA 系统弹窗，只能验到 output() 为止）；
  // qcSync 要真跑通得有后端 + 盘阵根，所以只在 test-manual-scene 那套里调。
  qcImport: (name: string, text: string) => boolean;
  qcClose: () => void;
  qcSetStatus: (name: string, s: QcStatus) => void;
  /** 要写回文档的全文（上半部分原文 + 终态行）。 */
  qcOutput: () => string;
  /** 设置写回目标路径（等价用户往页脚输入框里粘）。 */
  qcSetTarget: (path: string) => void;
  /** 把整份文档写回盘阵上那份 .txt（等价点页脚「同步」）。false = 失败，原因见错误条。 */
  qcSync: () => Promise<boolean>;
  qcState: () => {
    loaded: boolean;
    sourceName: string;
    /** 导入时判定的原编码（写回就按它写）。 */
    sourceEncoding: string;
    targetPath: string;
    total: number;
    done: number;
    selName: string | null;
    statuses: Record<string, QcStatus>;
  };
  /** 按生产全名去盘阵开场景（真机验收用；外网开发机没有盘阵，必然报错）。 */
  qcOpenByName: (name: string) => Promise<string>;
  // 图像对比（关闭 / 点选对比 / 分屏对比，2026-09-20）
  /** 当前模式。 */
  cmpMode: () => import('../lib/compare.js').CompareMode;
  /** 切模式（等效对比条三选一）。 */
  setCmpMode: (m: import('../lib/compare.js').CompareMode) => void;
  /** 对比条是否展开。 */
  cmpStripOpen: () => boolean;
  setCmpStripOpen: (open: boolean) => void;
  /** 两格的渲染输入：每格的视口矩形、视图变换、上面那张 rec 的 id、是不是活动侧。
   *  单屏时只有一条（rect 铺满画布）。 */
  cmpPanes: () => {
    side: 'A' | 'B';
    rect: { x: number; y: number; w: number; h: number };
    view: { scale: number; ox: number; oy: number };
    recId: number | null;
    active: boolean;
  }[];
  /** 活动侧（分屏下决定掩码/云量/任务状态跟着谁）。 */
  activeSide: () => 'A' | 'B';
  setActiveSide: (s: 'A' | 'B') => void;
  /** 分隔比例（0.5 = 正中）。 */
  splitRatio: () => number;
  setSplitRatio: (r: number) => void;
  /** 分隔线在**画布局部**坐标里的位置（= 左格宽；单屏时等于画布宽）。
   *  落点判据与 `paneAtX` 都用它，所以 e2e 要能直接读到，别自己再算一遍。 */
  splitX: () => number;
  /** 回正（比例回 0.5 且两侧重新适配）。 */
  cmpReset: () => void;
  /** 落位提示（dragover 期间为真，500ms 无新事件自己熄）。 */
  dragHint: () => { active: boolean; side: 'A' | 'B' | null };
  /** 点选清单里的 rec id（有序）。 */
  cmpList: () => number[];
  /** 把一条移出点选清单（等效行内「清除」）。 */
  cmpClear: (id: number) => void;
  /** 右侧栏是否展开 + 进分屏前的记忆值（撤销自动收起用）。 */
  ctxRail: () => { open: boolean; prev: boolean | null };
  setCtxRail: (open: boolean) => void;
  /** 右上角设置浮层是否开着。 */
  settingsOpen: () => boolean;
  setSettingsOpen: (open: boolean) => void;
  /** 对比模式后台预取开关（默认关；持久化在 localStorage['sr.viewer.cmpPrefetch']）。 */
  cmpPrefetchOn: () => boolean;
  setCmpPrefetch: (on: boolean) => void;
  /** 本地预览 blob 缓存的现状（条数 / 字节 / 上限）。 */
  previewCacheStats: () => { count: number; bytes: number; maxBytes: number };
  clearPreviewCache: () => void;
  /** 右下角任务提醒栈的现状（条数 + 连接状态）。提醒是**应用级**的，所以在查看器页
   *  里也能观测到「别的页面上发生的事」——浏览器回归就是靠它验跨页常驻。 */
  notices: () => {
    items: { id: number; kind: string; title: string; text: string; reason: string }[];
    connected: boolean;
    reconnecting: boolean;
  };
  /** 直接塞一条提醒（不经过 SSE）。浏览器回归用它验栈的渲染与点击跳转 ——
   *  真跑一条 SR 作业到 COMPLETED 不在回归的时间预算里。 */
  pushNotice: (draft: {
    key: string; taskId: number; kind: 'ok' | 'fail';
    title: string; text: string; reason: string; persist: boolean;
  }) => void;
  dismissNotice: (id: number) => void;
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
    lqPath: rec.lqPath,
    serverMaskPath: rec.serverMaskPath ?? null,
    thumb: (rec.thumb as unknown as HTMLCanvasElement | null),
    paintedMode: rec.paintedMode,
    sceneId: rec.sceneId,
    sceneDir: rec.sceneDir ?? null,
    stageKind: rec.stageKind ?? null,
    stageLabel: rec.stageLabel ?? null,
    linkNote: rec.linkNote,
    warmed: !!rec.thumb && rec.warmed === rec.thumb,
  };
}

/** 构建并挂到 window.__viewer（幂等；始终暴露给 e2e 与真机验收） */
export function mountE2EHooks(): ViewerHook {
  const hook: ViewerHook = {
    planExport,
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
    openSceneJpg: (meta, blob) => useViewerStore().openSceneJpg(meta, blob),
    openLocalImage: (file) => useViewerStore().openLocalImage(file),
    submitSr: () => useViewerStore().submitSr(),
    openScenePath: (p) => useViewerStore().openScenePath(p),
    tryLinkScenes: () => useViewerStore().tryLinkScenes(),
    bakeMaskToServer: () => useViewerStore().bakeMaskToServer(),
    modal: () => useViewerStore().modal,
    hideModal: () => useViewerStore().hideModal(),
    overlayVisible: () => !!useViewerStore().overlay.visible,
    recs: () => useViewerStore().recs.map(summarize),
    activeRec: () => {
      const rec = useViewerStore().activeRec;
      return rec ? summarize(rec) : null;
    },
    activeStretch: () => useViewerStore().activeStretch,
    setStretch: (m) => useViewerStore().setStretch(m),
    previewDiv: () => useViewerStore().previewDiv,
    setPreviewDiv: (div) => useViewerStore().setPreviewDiv(div),
    qcImport: (name, text) => useQcListStore().importText(name, text, 'utf-8'),
    qcClose: () => useQcListStore().close(),
    qcSetStatus: (name, s) => useQcListStore().setStatus(name, s),
    qcOutput: () => useQcListStore().output(),
    qcSetTarget: (path) => useQcListStore().setTarget(path),
    qcSync: () => useQcListStore().syncToTarget(),
    qcState: () => {
      const qc = useQcListStore();
      return {
        loaded: qc.loaded,
        sourceName: qc.sourceName,
        sourceEncoding: qc.sourceEncoding,
        targetPath: qc.targetPath,
        total: qc.counts.total,
        done: qc.counts.done,
        selName: qc.selName,
        statuses: { ...qc.statuses },
      };
    },
    qcOpenByName: (name) => useScenesStore().openByName(name),
    cmpMode: () => useViewerStore().compareMode,
    setCmpMode: (m) => useViewerStore().setCompareMode(m),
    cmpStripOpen: () => useViewerStore().cmpStripOpen,
    setCmpStripOpen: (open) => useViewerStore().setCmpStripOpen(open),
    cmpPanes: () => useViewerStore().panes.map((p) => ({
      side: p.side,
      rect: { ...p.rect },
      view: { ...p.view },
      recId: p.rec ? p.rec.id : null,
      active: p.active,
    })),
    activeSide: () => useViewerStore().activeSide,
    setActiveSide: (s) => useViewerStore().setActiveSide(s),
    splitRatio: () => useViewerStore().splitRatio,
    setSplitRatio: (r) => useViewerStore().setSplitRatio(r),
    splitX: () => useViewerStore().splitX,
    cmpReset: () => useViewerStore().resetSplit(),
    dragHint: () => ({ ...useViewerStore().dragHint }),
    cmpList: () => [...useViewerStore().compareList],
    cmpClear: (id) => useViewerStore().clearCompareEntry(id),
    ctxRail: () => ({
      open: useViewerStore().ctxRailOpen,
      prev: useViewerStore().ctxRailPrevOpen,
    }),
    setCtxRail: (open) => useViewerStore().setCtxRailOpen(open),
    settingsOpen: () => useViewerStore().settingsOpen,
    setSettingsOpen: (open) => useViewerStore().setSettingsOpen(open),
    cmpPrefetchOn: () => useViewerStore().cmpPrefetchOn,
    setCmpPrefetch: (on) => useViewerStore().setCmpPrefetch(on),
    previewCacheStats: () => useViewerStore().previewCacheStats(),
    clearPreviewCache: () => useViewerStore().clearPreviewCache(),
    notices: () => {
      const notices = useNoticesStore();
      const queue = useQueueStore();
      return {
        items: notices.items.map((n) => ({
          id: n.id, kind: n.kind, title: n.title, text: n.text, reason: n.reason,
        })),
        connected: queue.connected,
        reconnecting: queue.reconnecting,
      };
    },
    pushNotice: (draft) => useNoticesStore().push({ ...draft }),
    dismissNotice: (id) => useNoticesStore().dismiss(id),
  };
  if (window.__viewer !== hook) window.__viewer = hook;
  return hook;
}
