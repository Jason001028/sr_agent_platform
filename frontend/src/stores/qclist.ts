/**
 * stores/qclist.ts — 「待修复清单」状态（查看器 ROI/工具 面板置顶那块）
 * ------------------------------------------------------------------
 * 解析/写回的规则全在 lib/qclist.ts（纯函数、有单测）；本文件只管**状态与副作用**：
 * 当前清单、逐行状态、选中行、持久化、以及把内容写回盘阵上的 .txt。
 *
 * 持久化：localStorage 存「原文 + 状态表 + 写回目标路径」。刷新页面不该把一下午的
 * 标记弄丢；存原文而不是只存解析结果，是为了刷新后重新走一遍 parse（规则改了也能自愈）。
 */
import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import {
  parseQcList, buildQcDoc, decodeQcBytes, isTerminal, QC_STATUS_LABEL,
} from '../lib/qclist.js';
import type { QcIssue, QcList, QcStatus, QcEncoding } from '../lib/qclist.js';
import { loadSrConfig } from '../lib/scene.js';
import { apiWriteQcList } from '../lib/api.js';
import { pathLeafOf } from './queue.js';
import { useViewerStore } from './viewer.js';

const LS_KEY = 'sr.viewer.qcList';

interface Persisted {
  v: 1;
  name: string;
  enc: QcEncoding;
  text: string;
  statuses: Record<string, QcStatus>;
  /** 写回目标（用户粘的盘阵路径）。老缓存里没有这两个字段，读的时候都得兜底。 */
  path?: string;
  mtime?: number | null;
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
  /** 写回目标：盘阵上那份 .txt 的路径（用户粘的，既是输入也是显示）。 */
  const targetPath = ref('');
  /** 导入那一刻源文件的 mtime（秒）。**只有从 File 导入才有** —— 写回时带上给后端
   *  对护栏：盘阵上的清单在导入之后被别人改过就拒写。粘文本导入的没有时间戳可言，
   *  留 null（后端见了就不查这一项）。 */
  const sourceMtime = ref<number | null>(null);

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
        path: targetPath.value,
        mtime: sourceMtime.value,
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
      targetPath.value = typeof p.path === 'string' ? p.path : '';
      sourceMtime.value = typeof p.mtime === 'number' ? p.mtime : null;
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
    sourceMtime.value = null;          // 粘文本进来的，没有「源文件时间」可言
    selName.value = null;
    persist();
    return true;
  }

  /** 从 File 导入：先解编码（老记事本存的 ANSI 是 GBK，按 UTF-8 读整份乱码）。 */
  async function importFile(file: File): Promise<boolean> {
    const buf = await file.arrayBuffer();
    const { text, encoding } = decodeQcBytes(buf);
    if (importText(file.name, text, encoding)) {
      // 记住源文件的时间戳：路径是用户粘的、文件在盘阵上，两者只有这处对得上，
      // 写回时带过去让后端拦住「导入之后别人又改了一版」。
      sourceMtime.value = file.lastModified / 1000;
      persist();
      return true;
    }
    useViewerStore().showErr('「' + file.name + '」里没解析出问题行，没敢拿它替换当前清单');
    return false;
  }

  function close(): void {
    list.value = null;
    statuses.value = {};
    sourceName.value = '';
    sourceText.value = '';
    sourceMtime.value = null;
    selName.value = null;
    // 目标路径跟着清单一起清：换一份清单还留着上一份的路径，下一次「同步」就会
    // 把新清单盖到旧文档上。宁可让用户重粘一次。
    targetPath.value = '';
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

  /** 把 output() 写回盘阵上那份 .txt（原地覆盖）。
   *
   *  目标**由用户粘路径指定**，不再弹文件选择器：浏览器的文件选择器只能选到用户本机
   *  的文件，而清单在盘阵上 —— 真机页面还是 http，那个 API 压根不存在（写盘改走后端
   *  POST /api/qclist/write，契约见 api-contract.md §3.7）。
   *  「哪一份清单」本来也该由人确认：错一个字符就是盖掉另一个场景的记录。
   *  代价是「路径与文件对不对得上」只能靠后端 stat 与导入时记下的 mtime（见 sourceMtime）。 */
  async function syncToTarget(): Promise<boolean> {
    const viewer = useViewerStore();
    if (!list.value) {
      viewer.showErr('还没有导入清单');
      return false;
    }
    const path = targetPath.value.trim();
    if (!path) {
      viewer.showErr('先填要写回的清单路径 —— 盘阵上那份 .txt 的完整路径');
      return false;
    }
    try {
      const res = await apiWriteQcList(loadSrConfig(), {
        path,
        text: output(),
        encoding: sourceEncoding.value,
        ...(sourceMtime.value === null ? {} : { mtime: sourceMtime.value }),
      });
      persist();          // 顺手把用户刚粘的路径存下来，刷新后不用重粘
      // 展示用后端解析后的路径（与 /api/masks 同口径）：用户粘的可能是 W:\ 形态，
      // 而写下去的是它译出来的盘阵路径，回显这个才说明白「到底写进了哪一份」。
      viewer.showToast(
        '已同步至 ' + res.path + '（' + res.encoding.toUpperCase() + '，' + res.bytes + ' 字节）',
      );
      return true;
    } catch (e) {
      // 后端的 detail 是中文原话（路径不在白名单、不是 .txt、mtime 对不上、目录不可写…），
      // 原样转给用户比前端再猜一遍强。
      viewer.showErr('写回失败：' + errMsg(e));
      return false;
    }
  }

  /** 设置写回目标路径（输入框用）。空串 = 清除。 */
  function setTarget(path: string): void {
    targetPath.value = path;
  }

  /** 状态 → 中文标签（组件直接用，省得到处 import）。 */
  function labelOf(s: QcStatus | undefined): string {
    return s ? QC_STATUS_LABEL[s] : '';
  }

  restore();

  return {
    // 状态
    list, issues, statuses, loaded, counts,
    sourceName, sourceEncoding, selName, selIssue, targetPath,
    // 动作
    importText, importFile, close,
    setStatus, statusOf, labelOf,
    noteSubmitted, noteSubmittedDirs, select, selectForScene,
    output, syncToTarget, setTarget,
  };
});
