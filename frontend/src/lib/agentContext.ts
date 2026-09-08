/**
 * agentContext.ts — Agent 会话上下文快照纯函数（阶段6 ContextPanel Agent tab）
 * ------------------------------------------------------------------
 * 「每个发送自动附当前场景快照 + 选中 ROI 确定性统计」—— 这里把 ViewerRec /
 * RoiStats 的**数字**排版成一行行可给 LLM 读的附文；发送方拼
 * `question + '\n\n' + buildContextNote(snap)` 后走既有 chat store send（与 /chat
 * 同一会话、同一消息形状，不引入独立 system 层）。
 *
 * 纪律：快照里的每一个数字都来自确定性来源 —— scene_id/W/H/拉伸 = 当前 rec，
 * ROI 统计 = buildStats（显示层 8bit）；AI 只可引用/解读这些数，不得自行编造。
 * 因此注记文本把阈值语义（亮像元 ≥ hi / 过曝 ≥ clip）显式带上，并提示只读参考。
 */
import { STAT_HI, STAT_CLIP } from './roiStats.js';
import type { RoiStats, RoiGeom } from './roiStats.js';

/** 单个 ROI 附给 Agent 的确定性数字快照。 */
export interface RoiCtxNote {
  /** 1-based 显示编号（= 面板 ROI 列表 #N，与几何/统计同一 N）。 */
  index: number;
  /** 原图像素尺度 bbox 尺寸 + 鞋带面积（确定性几何，非统计）。 */
  geom: RoiGeom;
  /** 显示层 8bit 像素统计（buildStats 结果）。 */
  stats: RoiStats;
}

/** 当前查看器上下文输入（viewer store → buildContextNote 的纯数据桥）。 */
export interface ViewerContextSnap {
  name: string;
  /** 盘阵场景 id（route='jpg' 时非空；本地文件为 null —— Agent tab 本就禁用）。 */
  sceneId: string | null;
  /** 原图（全分辨率）宽高。 */
  W: number;
  H: number;
  /** 显示画布（当前显示层）宽高。 */
  dispW: number;
  dispH: number;
  /** 拉伸模式展示名（如 '线性 2%' / '线性'），Agent 据此知道像素含义。 */
  stretch: string;
  /** 选中 ROI 的统计快照；无选中 → null（不附 ROI 数字）。 */
  roi: RoiCtxNote | null;
}

function n1(v: number | null): string {
  return v === null ? '—' : (Math.round(v * 10) / 10).toString();
}
function pct(v: number): string {
  return (Math.round(v * 10) / 10).toString();
}
function areaTxt(a: number): string {
  if (a >= 1e6) return (a / 1e6).toFixed(2) + 'M px²';
  if (a >= 1e3) return (a / 1e3).toFixed(1) + 'k px²';
  return a + ' px²';
}

/** 排版选中 ROI 的确定性统计行（核心附文；数字即事实，禁止 AI 编造）。 */
export function roiStatLine(r: RoiCtxNote): string {
  const s = r.stats, g = r.geom;
  const kind = s.n === 0
    ? '（该 ROI 内无有效显示像元：空选区 / 全透明）'
    : '';
  return 'ROI#' + r.index
    + ' bbox=' + g.w + '×' + g.h + 'px 面积≈' + areaTxt(g.area)
    + ' → 显示层统计: n=' + s.n
    + ' min=' + n1(s.min) + ' max=' + n1(s.max)
    + ' mean=' + n1(s.mean) + ' std=' + n1(s.std)
    + ' 亮像元(≥' + STAT_HI + ')=' + pct(s.hiPct) + '%'
    + ' 过曝(≥' + STAT_CLIP + ')=' + pct(s.clipPct) + '%'
    + (s.sampled ? ' [抽行 stride=' + s.stride + ']' : '')
    + kind;
}

/** 把当前查看器上下文排版成一行行附文（question 之后发送）。 */
export function buildContextNote(c: ViewerContextSnap): string {
  const head = '【查看器上下文 · 只读附文】以下为当前画面确定性状态，供解读参考，';
  const lines: string[] = [];
  lines.push(head + '所有数值不可自行推算或编造：');
  lines.push('图像: "' + c.name + '"'
    + ' scene=' + (c.sceneId ?? '本地(无 sceneId)')
    + ' 原图=' + c.W + '×' + c.H
    + ' 显示=' + c.dispW + '×' + c.dispH
    + ' 拉伸=' + (c.stretch || '?'));
  if (c.roi) lines.push(roiStatLine(c.roi));
  else lines.push('未选中 ROI（无 ROI 统计附文；如需数字请先在左侧框选 ROI 再提问）。');
  lines.push('若用户提到别的区域/别的场景数字，先声明那是估值或需重新框选，不能当作本附文数字使用。');
  return lines.join('\n');
}

/** 发送给 Agent 的整段用户消息 = question + 分隔线 + 上下文附文。
    分隔线供 Agent 侧气泡把「用户原话」与「自动附的只读上下文」视觉分开。 */
export const CTX_DIVIDER = '——— 查看器上下文（自动附，只读，勿当用户原话）———';

export function agentPayload(question: string, note: string): string {
  return question + '\n' + CTX_DIVIDER + '\n' + note;
}
