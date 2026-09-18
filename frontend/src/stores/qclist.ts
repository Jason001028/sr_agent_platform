/**
 * stores/qclist.ts — 「待修复清单」状态（查看器 ROI/工具 面板置顶那块）
 * ------------------------------------------------------------------
 * 解析/写回的规则全在 lib/qclist.ts（纯函数、有单测）；本文件只管**状态与副作用**：
 * 当前清单、逐行状态、选中行、持久化、以及把内容写回盘阵上的 .txt。
 *
 * 持久化：localStorage 存「原文 + 状态表」。刷新页面不该把一下午的标记弄丢；
 * 存原文而不是只存解析结果，是为了刷新后重新走一遍 parse（规则改了也能自愈）。
 * 同步用的**文件句柄不持久化** —— FileSystemFileHandle 要存 IndexedDB，而那个
 * 句柄过一阵子还会失效变只读，存了反而给用户一个点不动的按钮。刷新后重选一次
 * 目标文件，代价可接受。
 */
import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import {
  parseQcList, buildQcDoc, encodeQcText, decodeQcBytes, isTerminal, QC_STATUS_LABEL,
} from '../lib/qclist.js';
import type { QcIssue, QcList, QcStatus, QcEncoding } from '../lib/qclist.js';
import { pathLeafOf } from './queue.js';
import { useViewerStore } from './viewer.js';

const LS_KEY = 'sr.viewer.qcList';

interface Persisted {
  v: 1;
  name: string;
  enc: QcEncoding;
  text: string;
  statuses: Record<string, QcStatus>;
}

/* ---------------- File System Access API 的最小声明 ----------------
   TS 5.6 的 lib.dom 里还没有 showOpenFilePicker / createWritable，这里补最小形状。
   不图省事写 any：写盘的参数写错要到运行时才炸，而这功能一炸就是覆盖用户文件。 */
interface FsaWritable {
  write(data: Blob): Promise<void>;
  close(): Promise<void>;
}
interface FsaHandle {
  readonly name: string;
  createWritable(): Promise<FsaWritable>;
}
type FsaPicker = (opts?: {
  types?: { description?: string; accept: Record<string, string[]> }[];
  multiple?: boolean;
}) => Promise<FsaHandle[]>;

/** 拿 picker，拿不到返回 null（Firefox/Safari 没有这个 API）。 */
function fsaPicker(): FsaPicker | null {
  const p = (window as unknown as { showOpenFilePicker?: FsaPicker }).showOpenFilePicker;
  return typeof p === 'function' ? p : null;
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export const useQcListStore = defineStore('qclist', () => {
  /** 当前清单（parse 结果；null = 没导入）。 */
  const list = ref<QcList | null>(null);
  /** 逐行状态，按生产全名索引（不在表里 = 还没动过）。 */
  const statuses = ref<Record<string, QcStatus>>({});
  /** 导入的文件名 / 原文 / 原编码（写回时要按原编码、原换行符还原）。 */
  const sourceName = ref('');
  const sourceText = ref('');
  const sourceEncoding = ref<QcEncoding>('utf-8');
  /** 当前选中的行（点行、或当前打开的图自动命中）。 */
  const selName = ref<string | null>(null);
  /** 同步目标文件名（仅显示用；句柄本身在模块变量里，见下）。 */
  const targetName = ref('');

  /** 同步目标的文件句柄。**不是 ref** —— 它不需要参与渲染，塞进响应式只会让
   *  Vue 去代理一个宿主对象。选过一次就留着，之后一键同步不再弹窗。 */
  let targetHandle: FsaHandle | null = null;

  const issues = computed<QcIssue[]>(() => list.value?.issues ?? []);
  const loaded = computed(() => list.value !== null);
  const selIssue = computed<QcIssue | null>(
    () => issues.value.find((i) => i.name === selName.value) ?? null,
  );

  /** 计数：total 全部行；done 只数**终态**（中间态不算完成，与写回口径一致）。 */
  const counts = computed(() => {
    const by: Record<QcStatus, number> = {
      drawn: 0, submitted: 0, fixed: 0, rejected: 0, no_blur: 0,
    };
    let done = 0;
    for (const it of issues.value) {
      const s = statuses.value[it.name];
      if (!s) continue;
      by[s] += 1;
      if (isTerminal(s)) done += 1;
    }
    return { total: issues.value.length, done, by };
  });

  function statusOf(name: string): QcStatus | undefined {
    return statuses.value[name];
  }

  /* ---------------- 持久化 ---------------- */

  function persist(): void {
    try {
      if (typeof localStorage === 'undefined' || !list.value) return;
      const payload: Persisted = {
        v: 1,
        name: sourceName.value,
        enc: sourceEncoding.value,
        text: sourceText.value,
        statuses: statuses.value,
      };
      localStorage.setItem(LS_KEY, JSON.stringify(payload));
    } catch { /* 隐私模式 / 配额满：放弃持久化，不影响本次会话 */ }
  }

  function dropPersisted(): void {
    try {
      if (typeof localStorage !== 'undefined') localStorage.removeItem(LS_KEY);
    } catch { /* 同上 */ }
  }

  function restore(): void {
    try {
      if (typeof localStorage === 'undefined') return;
      const raw = localStorage.getItem(LS_KEY);
      if (!raw) return;
      const p = JSON.parse(raw) as Persisted;
      if (!p || p.v !== 1 || typeof p.text !== 'string') { dropPersisted(); return; }
      const parsed = parseQcList(p.text);
      if (!parsed.issues.length) { dropPersisted(); return; }   // 解不出东西的缓存没意义
      list.value = parsed;
      sourceName.value = typeof p.name === 'string' ? p.name : '';
      sourceText.value = p.text;
      sourceEncoding.value = p.enc === 'gbk' ? 'gbk' : 'utf-8';
      // 状态以缓存为准（它含还没同步出去的改动），文件下半部分只作首次导入的起点。
      statuses.value = { ...(p.statuses ?? {}) };
    } catch {
      dropPersisted();
    }
  }

  /* ---------------- 导入 / 关闭 ---------------- */

  /** 导入文本。返回是否真的换上了。
   *
   *  **一份问题行都解不出来的文本一律拒绝**：拖拽入口就在画布上（拖 .txt 即导入），
   *  用户完全可能顺手把「掩膜中心点坐标.txt」之类的文件丢进来 —— 若照单全收，
   *  已经标了一下午的清单就被一个不相关的 txt 冲没了，而且悄无声息。
   *  宁可什么都不做并出个声，也不要这种数据丢失。 */
  function importText(name: string, text: string, enc: QcEncoding): boolean {
    const parsed = parseQcList(text);
    if (!parsed.issues.length) return false;
    list.value = parsed;
    // 换一份清单 = 以下半部分为准重建状态，不继承上一份的标记（名字多半对不上）。
    statuses.value = { ...parsed.statuses };
    sourceName.value = name;
    sourceText.value = text;
    sourceEncoding.value = enc;
    selName.value = null;
    persist();
    return true;
  }

  /** 从 File 导入：先解编码（老记事本存的 ANSI 是 GBK，按 UTF-8 读整份乱码）。 */
  async function importFile(file: File): Promise<boolean> {
    const buf = await file.arrayBuffer();
    const { text, encoding } = decodeQcBytes(buf);
    if (importText(file.name, text, encoding)) return true;
    useViewerStore().showErr('「' + file.name + '」里没解析出问题行，没敢拿它替换当前清单');
    return false;
  }

  function close(): void {
    list.value = null;
    statuses.value = {};
    sourceName.value = '';
    sourceText.value = '';
    selName.value = null;
    targetName.value = '';
    targetHandle = null;
    dropPersisted();
  }

  /* ---------------- 逐行状态 ---------------- */

  /** 点选状态。**再点当前那个 = 取消**（写成 toggle，省一个「清除」按钮）。 */
  function setStatus(name: string, s: QcStatus): void {
    if (statuses.value[name] === s) delete statuses.value[name];
    else statuses.value[name] = s;
    persist();
  }

  /** 提交 SR 后把该行推进到「已提交任务」。
   *  只在「还没标」或「停在三段式第一段」时推进 —— 终态是人眼判出来的结论，
   *  不能被队列状态覆盖，更不该倒退回去。 */
  function noteSubmitted(name: string): void {
    if (!list.value || !name) return;
    if (!list.value.issues.some((i) => i.name === name)) return;
    const cur = statuses.value[name];
    if (cur === undefined || cur === 'drawn') {
      statuses.value[name] = 'submitted';
      persist();
    }
  }

  /** 批量版：直接吃队列任务行的 lq_path（末段就是生产全名）。 */
  function noteSubmittedDirs(dirs: (string | null | undefined)[]): void {
    for (const d of dirs) if (d) noteSubmitted(pathLeafOf(d));
  }

  /** 选中某一行；传 null 清空。 */
  function select(name: string | null): void {
    selName.value = name;
  }

  /** 当前打开的图 → 自动选中清单里对应的那一行。
   *
   *  匹配**必须走 lqPath 的末段**而不是 rec.name：RC 场景的输入影像是 PAN.tif，
   *  那条路的 rec.name 是个没用的 "PAN"，只有场景目录名才是清单里的生产全名。
   *  本地随便打开的图没有 lqPath → 匹配不上，面板只当列表看。 */
  function selectForScene(lqPath: string | null | undefined): void {
    if (!loaded.value) return;
    const name = lqPath ? pathLeafOf(lqPath) : '';
    if (!name) return;
    if (issues.value.some((i) => i.name === name)) selName.value = name;
  }

  /* ---------------- 写回 ---------------- */

  /** 生成要写回文档的全文（上半部分原文 + 下半部分终态行）。 */
  function output(): string {
    return list.value ? buildQcDoc(list.value, statuses.value) : '';
  }

  /** 把 output() 写回指定的 .txt（原地覆盖）。
   *  第一次点会弹文件选择器，之后句柄留着，再点就是纯粹的一键同步。 */
  async function syncToTarget(): Promise<boolean> {
    const viewer = useViewerStore();
    if (!list.value) {
      viewer.showErr('还没有导入清单');
      return false;
    }

    if (!targetHandle) {
      const pick = fsaPicker();
      if (!pick) {
        viewer.showErr('这个浏览器不支持「原地覆盖」写盘（需要 Edge / Chrome，且本页得是 https 或 localhost）');
        return false;
      }
      try {
        // 必须带上 window 当接收者：解构出来直接调会抛 Illegal invocation。
        const picked = await pick.call(window, {
          types: [{ description: '待修复清单', accept: { 'text/plain': ['.txt'] } }],
        });
        targetHandle = picked[0] ?? null;
      } catch {
        return false;                       // 用户取消选择器：不出声
      }
      if (!targetHandle) return false;
      targetName.value = targetHandle.name;
    }

    const { blob, fellBack } = encodeQcText(output(), sourceEncoding.value);
    try {
      const w = await targetHandle.createWritable();
      await w.write(blob);
      await w.close();
      viewer.showToast(
        '已同步至 ' + targetName.value
        + (fellBack ? '（原文件是 GBK：浏览器编不出 GBK，本次按 UTF-8 带 BOM 写回，记事本/Excel 能正常显示）' : ''),
      );
      return true;
    } catch (e) {
      // 权限被收回（句柄过期）→ 丢掉句柄，下次点重新选，否则这按钮会永远点不动。
      if ((e as DOMException)?.name === 'NotAllowedError') {
        targetHandle = null;
        targetName.value = '';
      }
      viewer.showErr('写盘失败：' + errMsg(e) + ' —— 文件若正被记事本 / Excel 打开，关掉再试');
      return false;
    }
  }

  /** 忘掉同步目标（用户想换一份文档写）。 */
  function forgetTarget(): void {
    targetHandle = null;
    targetName.value = '';
  }

  /** 状态 → 中文标签（组件直接用，省得到处 import）。 */
  function labelOf(s: QcStatus | undefined): string {
    return s ? QC_STATUS_LABEL[s] : '';
  }

  restore();

  return {
    // 状态
    list, issues, statuses, loaded, counts,
    sourceName, sourceEncoding, selName, selIssue, targetName,
    // 动作
    importText, importFile, close,
    setStatus, statusOf, labelOf,
    noteSubmitted, noteSubmittedDirs, select, selectForScene,
    output, syncToTarget, forgetTarget,
  };
});
