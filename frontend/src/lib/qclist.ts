/**
 * qclist.ts — 「待修复清单」.txt 的解析与写回（纯函数，无 DOM / 无 store 依赖）
 * ------------------------------------------------------------------
 * 清单固定两段结构（质检部门给的上半 + 我们自己补的下半，用制表符分列）：
 *
 *   JL1KF02B04_PMS03_..._0020_001_L1,\t产品存在伪影 (问题类型:产品存在伪影 行列号:7300.26,1737.98 影像类型:pan )\t李佳峻
 *   ...
 *   （空行）
 *   JL1KF02B04_PMS03_..._0020_001_L1\t修复通过
 *
 * **上半部分逐字保留**：写回时不是照着解析结果重新拼一遍，而是把原文里除状态行
 * 之外的每一行原样吐回去。解析只用来「显示 + 认名字」，认不出来的行也照样留在
 * 上半部分 —— 质检部门的原始记录不允许因为我们读不懂就少一行。
 *
 * 坐标约定有一处**与项目其余部分相反**，改代码前务必看清：
 *   清单里的「行列号:30766.11,21862.51」按中文习惯是 **(行, 列)**；
 *   而 lib/viewMath.ts 与 store.locatePixel(x, y) 是 **x=列、y=行**。
 *   所以从清单跳转必须写 `locatePixel(col, row)`，顺序反了会跳到画面另一头。
 */

/* ---------------- 状态 ---------------- */

/** 五个互斥状态：前三个是三段式进度（可点选推进），后两个是人工终态。 */
export type QcStatus = 'drawn' | 'submitted' | 'fixed' | 'rejected' | 'no_blur';

/** 三段式进度条的阶段顺序（UI 按这个顺序排）。 */
export const QC_STAGES: QcStatus[] = ['drawn', 'submitted', 'fixed'];

/** 人工终态（不走三段式，单独两个按钮）。 */
export const QC_FINALS: QcStatus[] = ['rejected', 'no_blur'];

export const QC_STATUS_LABEL: Record<QcStatus, string> = {
  drawn: '已绘制掩码',
  submitted: '已提交任务',
  fixed: '已修复',
  rejected: '驳回',
  no_blur: '非模糊通过',
};

/** 状态 → 写进文档下半部分的词。**只有终态有词**：中间态是本地进度，
 *  不落到质检文档里（未完成的行干脆不出现，见 buildQcDoc）。 */
export const QC_DOC_WORD: Partial<Record<QcStatus, string>> = {
  fixed: '修复通过',
  rejected: '驳回',
  no_blur: '非模糊通过',
};

/** 反向：文档里的词 → 状态。读清单下半部分用；额外的别名只为容错。 */
const DOC_WORD_TO_STATUS: Record<string, QcStatus> = {
  '修复通过': 'fixed',
  '已修复': 'fixed',
  '驳回': 'rejected',
  '非模糊通过': 'no_blur',
};

export function isTerminal(s: QcStatus | undefined): boolean {
  return s === 'fixed' || s === 'rejected' || s === 'no_blur';
}

/* ---------------- 数据结构 ---------------- */

export interface QcIssue {
  /** 生产全名（= 盘阵场景目录名；已去掉原文里尾随的半角/全角逗号）。 */
  name: string;
  /** 问题描述原文（第二列）。 */
  desc: string;
  /** 责任人（第三列）。 */
  owner: string;
  /** 从 desc 里抽出的「问题类型」；没写就是空串。 */
  kind: string;
  /** 「行列号」里的**行**（与 locatePixel 的 y 同义）。缺失为 null。 */
  row: number | null;
  /** 「行列号」里的**列**（与 locatePixel 的 x 同义）。缺失为 null。 */
  col: number | null;
  /** 「影像类型」（PAN / pan / PMS…）；没写就是空串。 */
  imgType: string;
}

export type QcEol = '\n' | '\r\n';

export interface QcList {
  /** 上半部分原文：原文里**除状态行之外**的所有行，按原顺序、逐字保留。
   *  内部统一用 \n 分隔（写回时按 eol 还原）；尾部空行已去掉（写回会补一个）。 */
  topRaw: string;
  /** 原文件用的换行符；写回复用同一种，别把别人的 CRLF 文件改成 LF。 */
  eol: QcEol;
  /** 上半部分解析出来的问题行，**按生产全名去重**（同名的多条伪影只留首次出现的
   *  那条）—— 下半部分一份名字只能有一行状态，不去重就会出现两条互相矛盾的记录。 */
  issues: QcIssue[];
  /** 从下半部分读到的既有状态（没出现过的名字不在表里）。 */
  statuses: Record<string, QcStatus>;
}

/* ---------------- 解析 ---------------- */

/** UTF-8 BOM 字符（U+FEFF）。写成 fromCharCode 而不是字面量：BOM 在编辑器里
 *  不可见，写成字面量的话以后没人看得出那里藏了一个字符（本文件被反复改坏过）。 */
const BOM = String.fromCharCode(0xFEFF);

function stripBom(s: string): string {
  return s.startsWith(BOM) ? s.slice(1) : s;
}

/** 第一列 → 生产全名：去首尾空白，再去掉尾随的逗号（原文里带一个，Excel 粘出来的痕迹）。 */
function cleanName(raw: string): string {
  return raw.trim().replace(/[,，]+$/, '').trim();
}

/** 状态行：恰好两列，且第二列是认得的终态词。认不出来的一律不当状态行
 *  （宁可留在上半部分原样写回，也不能把一个陌生词当成状态给吞掉）。 */
function parseStatusLine(raw: string): { name: string; status: QcStatus } | null {
  if (!raw.includes('\t')) return null;
  const f = raw.split('\t');
  if (f.length !== 2) return null;
  const name = cleanName(f[0]);
  const status = DOC_WORD_TO_STATUS[f[1].trim()];
  if (!name || !status) return null;
  return { name, status };
}

/** 问题行：≥3 列（名字 / 描述 / 责任人）。描述列里再抽结构化字段。 */
function parseIssueLine(raw: string): QcIssue | null {
  if (!raw.includes('\t')) return null;
  const f = raw.split('\t');
  if (f.length < 3) return null;
  const name = cleanName(f[0]);
  if (!name) return null;
  const desc = f[1].trim();
  const owner = f[2].trim();

  const kind = /问题类型[:：]\s*([^\s)）]+)/.exec(desc)?.[1] ?? '';
  const imgType = /影像类型[:：]\s*([^\s)）]+)/.exec(desc)?.[1] ?? '';
  const loc = /行列号[:：]\s*([+-]?\d+(?:\.\d+)?)\s*[,，]\s*([+-]?\d+(?:\.\d+)?)/.exec(desc);

  return {
    name, desc, owner, kind, imgType,
    row: loc ? Number(loc[1]) : null,
    col: loc ? Number(loc[2]) : null,
  };
}

/**
 * 解析整份清单文本。**不抛异常** —— 任何一行读不懂都只是不进 issues，
 * 内容仍在 topRaw 里原样保留，写回不会丢。
 */
export function parseQcList(text: string): QcList {
  const eol: QcEol = text.includes('\r\n') ? '\r\n' : '\n';
  const norm = stripBom(text.replace(/\r\n?/g, '\n'));

  const topLines: string[] = [];
  const statuses: Record<string, QcStatus> = {};
  const issues: QcIssue[] = [];
  const seen = new Set<string>();

  for (const raw of norm.split('\n')) {
    const st = parseStatusLine(raw);
    if (st) {
      // 同名状态行只认第一次：后面的重复行不再覆盖（写回时也只会生成一条）。
      if (!(st.name in statuses)) statuses[st.name] = st.status;
      continue;
    }
    topLines.push(raw);
    const iss = parseIssueLine(raw);
    if (iss && !seen.has(iss.name)) {
      seen.add(iss.name);
      issues.push(iss);
    }
  }

  return { topRaw: topLines.join('\n'), eol, issues, statuses };
}

/* ---------------- 写回 ---------------- */

/**
 * 生成要写回文档的全文 = 上半部分原文 + 空行 + 下半部分。
 *
 * 下半部分**整个重新生成**，只输出有终态的行（中间态与未处理的行不出现），
 * 顺序跟着上半部分走 —— 与原始文件的观感一致，也便于上下对照。
 */
export function buildQcDoc(list: QcList, statuses: Record<string, QcStatus>): string {
  const top = list.topRaw.split('\n');
  while (top.length && top[top.length - 1].trim() === '') top.pop();   // 尾部空行交给分隔符

  const bottom: string[] = [];
  for (const it of list.issues) {
    const s = statuses[it.name];
    const word = s ? QC_DOC_WORD[s] : undefined;   // 中间态 / 未处理 → undefined → 不出现
    if (word) bottom.push(it.name + '\t' + word);
  }

  const parts: string[] = [];
  if (top.length) parts.push(top.join(list.eol));
  if (bottom.length) parts.push(bottom.join(list.eol));
  return parts.length ? parts.join(list.eol + list.eol) + list.eol : '';
}

/* ---------------- 编码 ---------------- */

export type QcEncoding = 'utf-8' | 'gbk';

/**
 * 字节 → 文本。先按 UTF-8 严格解（带 BOM 先剥 BOM），解不动再退 GBK ——
 * 老版 Windows 记事本存中文 txt 默认是 ANSI(GBK)，按 UTF-8 读会整份乱码，
 * 而乱码的清单里一个名字都匹配不上，等于功能全废。
 * 两者都失败（GBK 解码器不可用且不是合法 UTF-8）时退回宽松 UTF-8，至少不崩。
 */
export function decodeQcBytes(buf: ArrayBuffer): { text: string; encoding: QcEncoding } {
  const bytes = new Uint8Array(buf);
  if (bytes.length >= 3 && bytes[0] === 0xEF && bytes[1] === 0xBB && bytes[2] === 0xBF) {
    return { text: new TextDecoder('utf-8').decode(bytes.subarray(3)), encoding: 'utf-8' };
  }
  try {
    return { text: new TextDecoder('utf-8', { fatal: true }).decode(bytes), encoding: 'utf-8' };
  } catch { /* 不是合法 UTF-8 → 大概率是 GBK */ }
  try {
    return { text: new TextDecoder('gbk').decode(bytes), encoding: 'gbk' };
  } catch {
    return { text: new TextDecoder('utf-8').decode(bytes), encoding: 'utf-8' };
  }
}

/**
 * 文本 → 要写盘的 Blob。原编码是 UTF-8 就照写；是 **GBK 则写不了** ——
 * 浏览器只有 UTF-8 编码器（TextEncoder 不认别的），编不出 GBK 字节。
 * 这种就退成 UTF-8 + BOM 并置 fellBack=true，由调用方明确告诉用户一声：
 * 现代记事本/Excel 认 BOM 能正常显示，但下游若有认 GBK 的脚本会乱码。
 */
export function encodeQcText(text: string, encoding: QcEncoding): { blob: Blob; fellBack: boolean } {
  if (encoding === 'gbk') {
    return { blob: new Blob([BOM + text], { type: 'text/plain' }), fellBack: true };
  }
  return { blob: new Blob([text], { type: 'text/plain' }), fellBack: false };
}
