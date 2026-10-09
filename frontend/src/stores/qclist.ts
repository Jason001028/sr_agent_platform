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
import type { SceneOpenMeta } from '../lib/scene.js';
import {
  apiWriteQcList, apiSceneSiblings, nosrItemOf, siblingRow,
} from '../lib/api.js';
import type { SceneResolveResult, SceneSiblings, SceneSibling } from '../lib/api.js';
import { matchesScene, runSceneBake, progressText, fmtElapsed } from '../lib/qcbatch.js';
import type { BakeFail } from '../lib/qcbatch.js';
import { pathLeafOf } from './queue.js';
import { useViewerStore } from './viewer.js';
import { useScenesStore } from './scenes.js';

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
      // 生成预览账**不进缓存**：它记的是「这次会话里生成过什么」，刷新后要重新生成就重跑一键解析，
      // 而不是让一份上次会话的旧账挂在新清单上。
      clearBakeLedger();
    } catch {
      dropPersisted();
    }
  }

  /* ---------------- 导入 / 关闭 ---------------- */

  /** 导入文本。返回是否真的换上了。
   *
   *  **一份问题行都解不出来的文本一律拒绝**：拖拽入口就在画布上（拖 .txt 即导入），
   *  用户完全可能同时把「掩膜中心点坐标.txt」之类的文件丢进来 —— 若照单全收，
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
    clearBakeLedger();                 // 键是行名，换一份清单即作废
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
    // 还跑着就先收手：清单都没了，再往里插卡片就是往一份不存在的清单里写账。
    bakeAbort?.abort();
    list.value = null;
    statuses.value = {};
    clearBakeLedger();
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

  /** 批量版：直接吃队列任务行的 lq_path（末段就是场景目录名 —— 它带产品段，而清单
   *  第一列多半不带，所以照旧走 `rowForScene` 那把补段的键，不能逐字比）。 */
  function noteSubmittedDirs(dirs: (string | null | undefined)[]): void {
    for (const d of dirs) {
      const hit = rowForScene(d);
      if (hit) noteSubmitted(hit.name);
    }
  }

  /** 选中某一行；传 null 清空。 */
  function select(name: string | null): void {
    selName.value = name;
  }

  /** 场景目录（或它的末段）→ 清单里对应的那一行（**文档顺序第一行**命中）。
   *
   *  匹配**绝不能逐字相等**：清单第一列约定俗成省掉产品段（`…_101_0020_001_L1`），
   *  而盘阵上的景级目录叫 `…_101_0020_001_L1_PAN` —— 逐字比就恒不成立，症状是
   *  「图开出来了，那一行却不选中、还一直显示『打开』而不是『当前』」。补段口径是
   *  `lib/qcbatch.ts` 里那份后端镜像；假阴性最坏是「没选中」，绝不会选中错的一行。
   *
   *  1:N 的口径：同一份清单里若同时列了 `…_L1` 与 `…_L1_PAN` 两行，两行都会命中同一
   *  个目录 —— 这里取文档顺序第一行（谁先写谁是那一景的代表）。 */
  function rowForScene(dirOrLeaf: string | null | undefined): QcIssue | null {
    if (!loaded.value) return null;
    const leaf = dirOrLeaf ? pathLeafOf(dirOrLeaf) : '';
    if (!leaf) return null;
    return issues.value.find((i) => matchesScene(i.name, i.imgType, leaf)) ?? null;
  }

  /** 当前打开的图 → 自动选中清单里对应的那一行。
   *
   *  用**场景目录**而不是 `lqPath` 当输入：中间产物（SR / NOSR）的 `lqPath` 可能为空
   *  而 `sceneDir` 一定有（见 viewer 的 ViewerRec.sceneDir）。两者都取不到（本地随便
   *  打开的图）→ 匹配不上，面板只当列表看。 */
  function selectForScene(dirOrLeaf: string | null | undefined): void {
    const hit = rowForScene(dirOrLeaf);
    if (hit) selName.value = hit.name;
  }

  /* ---------------- 一键解析：按 .txt 顺序批量生成预览 + 入列 ----------------
     一景两步：本体 jpg + NOSR jpg（盘上没有那份就只生成本体，如实记一笔「缺失」）。
     每一步都**只把 jpg 生成到盘上**，卡片入列时只有身份、没有像素 —— 几十景一次装进
     内存必爆（算式见 viewer 的 ViewerRec.card）。点开某张卡时命中服务端缓存，秒出。

     全程走既有端点（resolve / preview / siblings），**没有新后端接口**：批量端点既
     停不下服务端已经在生成的那一份，又要把「试过哪些候选、各自为什么不行」重写一遍
     （那就成了新的「静默换路径」入口）。服务端读盘本就该并发 1（后台主动生成 ticker 就是
     单消费者），省下的只有毫秒级往返。 */

  type BakeState = 'idle' | 'running' | 'stopping' | 'done' | 'stopped';

  const bakeState = ref<BakeState>('idle');
  const bakeTotal = ref(0);
  const bakeDone = ref(0);
  /** 进度行主文案（`第 3/12 景 · 名字 · ÷4`）。 */
  const bakeNow = ref('');
  /** 跑批已用的秒数（跑着时每秒刷一次，定格在结束那一刻）。 */
  const bakeElapsed = ref(0);
  /** 失败账：**单独一张表，绝不写 `statuses`** —— 那份会被 `buildQcDoc` 写回盘阵上
   *  的 .txt、还驱动 `counts.done`。生成图失败不是质检结论，不能污染文档。 */
  const bakeFails = ref<Record<string, BakeFail>>({});
  /** 盘上的事实（不是错误）：「没有未超分那份」。 */
  const bakeNotes = ref<Record<string, string>>({});

  let bakeAbort: AbortController | null = null;
  let bakeTimer: ReturnType<typeof setInterval> | null = null;

  function stopBakeTimer(): void {
    if (bakeTimer !== null) { clearInterval(bakeTimer); bakeTimer = null; }
  }

  /** 换清单 / 关清单时把三张账一起清掉（键是行名，上一份的行名在这份里没有意义）。 */
  function clearBakeLedger(): void {
    bakeState.value = 'idle';
    bakeTotal.value = 0;
    bakeDone.value = 0;
    bakeNow.value = '';
    bakeElapsed.value = 0;
    bakeFails.value = {};
    bakeNotes.value = {};
  }

  /** 本体卡的元数据 —— 与 `scenes.open` 建 meta 的口径逐字对齐（少一个字段就是
   *  「同一种入口两张卡长得不一样」）。
   *
   *  尺寸缺了**抛**（与 `scenes.open` 同一句口径：掩码按 rec.W/H 换算，0 会让落点全错）：
   *  批量这条路上「拒绝入列」比「留一张点了必炸的空卡」好，抛出去由驱动器记进失败账。 */
  function bodyMeta(r: SceneResolveResult): SceneOpenMeta {
    if (!r.row.W || !r.row.H) {
      throw new Error('「' + r.row.name + '」尺寸未知（元数据缺 W/H），无法换算掩码，拒绝打开');
    }
    return {
      name: r.row.name, W: r.row.W, H: r.row.H, sceneId: r.row.id,
      lqPath: r.row.lq_path, sceneDir: r.resolved.dir,
      serverMaskPath: r.resolved.mask_path,
      stageKind: r.resolved.kind, stageSuffix: r.resolved.suffix,
    };
  }

  /** NOSR 卡的元数据 —— 与 `viewer.openSceneSibling` 的口径**逐字对齐**（那张卡是同一种
   *  图的另一条入口，两边字段不一样就会出现「芯片打开说 A、批量入列说 B」）。
   *
   *  `lqPath` 取场景目录而**不是 null**：后端确实把 `row.lq_path` 对中间产物置了空，但
   *  `openSceneSibling` 传的是 `res.lqPath`（= 场景目录，任务区靠它关联队列行），批量
   *  这条入口照抄 —— 只读与否由 `stageKind` 决定（`isIntermediateStage` 那三处门），
   *  不看 lqPath。 */
  function nosrMeta(sib: SceneSiblings, item: SceneSibling): SceneOpenMeta {
    const stem = (item.name ?? 'NOSR').replace(/\.(tif|tiff|jpg|jpeg)$/i, '');
    return {
      name: stem, W: item.W ?? 0, H: item.H ?? 0,
      sceneId: item.id ?? '', lqPath: sib.lqPath, sceneDir: sib.lqPath,
      serverMaskPath: null,
      stageKind: 'nosr', stageSuffix: sib.suffix,
    };
  }

  /** 按 .txt 顺序把整份清单跑一遍：清空暂存区 → 逐景 resolve + 生成两份 jpg + 入列。
   *
   *  跑的过程中**不弹遮罩、不动 busy**（那是模态的，会把用户正在看的图挡住、工具栏
   *  锁住几十景 × 每景几十秒）；进度只走面板上那一行字。 */
  async function bakeAll(): Promise<void> {
    if (!list.value || bakeState.value === 'running' || bakeState.value === 'stopping') return;
    const rows = issues.value;
    if (!rows.length) return;
    const viewer = useViewerStore();
    const scenes = useScenesStore();
    // ① 先清空：用户先看见空的暂存区，再看它一景一景长出来。
    viewer.clearRecs();
    viewer.clearLights();
    clearBakeLedger();
    bakeTotal.value = rows.length;
    const div = viewer.previewDiv;         // 抓一次，整批沿用（中途拖滑块只影响后面那些景）
    const startedAt = Date.now();
    const ac = new AbortController();
    bakeAbort = ac;
    bakeState.value = 'running';
    stopBakeTimer();
    bakeTimer = setInterval(() => {
      bakeElapsed.value = Math.round((Date.now() - startedAt) / 1000);
    }, 1000);
    try {
      const report = await runSceneBake(rows, {
        resolve: (it, signal) => scenes.resolveByName(it.name, it.imgType, { signal }),
        body: async (it, res) => {
          // ① 入列一张空卡（带身份、带「第一次点开去哪儿取图」）② 把 jpg 生成到盘上。
          // 顺序不能反：降采样到功才入列的话，用户跑到一半看到的是「进度在走、左边还是空的」。
          const r = res as SceneResolveResult;
          await viewer.bakeCardPixels(viewer.insertSceneCard(bodyMeta(r), r.row));
        },
        nosr: async (it, res) => {
          const r = res as SceneResolveResult;
          const sib = await apiSceneSiblings(loadSrConfig(), r.row.id);
          const item = nosrItemOf(sib);
          // 「没有这一份」是盘上的事实，不是失败（口径见 api.ts::nosrItemOf）。
          // 尺寸读不出来同上：打不开就不入列，免得留一张点了必炸的空卡。
          if (!item || !item.id || item.W == null || item.H == null) return 'missing';
          await viewer.bakeCardPixels(
            viewer.insertSceneCard(nosrMeta(sib, item), siblingRow(sib, item)));
          return 'ok';
        },
        onProgress: (i, n, it) => {
          bakeDone.value = i - 1;
          bakeNow.value = progressText(i, n, it.name, div);
        },
      }, ac.signal);
      bakeDone.value = report.done;
      bakeFails.value = report.fails;
      bakeNotes.value = report.notes;
      bakeState.value = report.stopped ? 'stopped' : 'done';
    } finally {
      stopBakeTimer();
      bakeElapsed.value = Math.round((Date.now() - startedAt) / 1000);
      bakeNow.value = '';
      bakeAbort = null;
    }
  }

  /** 中途停：掐掉在飞的 resolve，并在下一景开跑之前收手。
   *  **正在生成的那一景停不下来** —— 取图的两个 API 都不收 AbortSignal，所以让它生成完
   *  （落盘正是用户要的缓存），文案如实写「正在停止（等这一景生成完…）」。 */
  function stopBake(): void {
    if (bakeState.value !== 'running') return;
    bakeState.value = 'stopping';
    bakeAbort?.abort();
  }

  /** 按钮上的字。跑着（含正在停止）时它是「停止」，其余时候是「一键解析」。 */
  const bakeHead = computed(() => (
    bakeState.value === 'running' || bakeState.value === 'stopping' ? '停止' : '一键解析'
  ));

  /** 按钮旁边那行小字：待命说清要干什么 → 跑着报进度与已用时长 → 收尾如实报账。
   *  **失败与「缺 NOSR」分开数**：前者是要处理的，后者是盘上的事实。 */
  const bakeLine = computed(() => {
    const st = bakeState.value;
    // 「每次按盘上最新源重新生成」这句是**必须的**：批量带 force、不复用旧预览，
    // 于是同一份清单连跑两次的耗时一样长。不说清楚，用户
    // 只会把「第二次怎么还这么慢」读成卡住了。
    if (st === 'idle') {
      return '按清单顺序逐景生成预览本体 + NOSR 两份 jpg（每次按盘上最新源重新生成），'
        + '每景左侧落两张卡';
    }
    if (st === 'stopping') return '正在停止（等这一景生成完…）';
    if (st === 'running') {
      return (bakeNow.value || '正在准备…') + ' · 已用 ' + fmtElapsed(bakeElapsed.value * 1000);
    }
    const nFail = Object.keys(bakeFails.value).length;
    const nNote = Object.keys(bakeNotes.value).length;
    let s = st === 'stopped'
      ? '已停止：跑完 ' + bakeDone.value + '/' + bakeTotal.value + ' 景'
      : '共 ' + bakeTotal.value + ' 景';
    s += ' · 成功 ' + Math.max(0, bakeDone.value - nFail);
    if (nFail) s += ' · 失败 ' + nFail;
    if (nNote) s += ' · 缺 NOSR ' + nNote;
    return s + ' · 用了 ' + fmtElapsed(bakeElapsed.value * 1000);
  });

  /** 失败账逐条摊开（名字 → 第几步 + 后端原话），给面板下面那块用。 */
  const bakeFailList = computed(() => (
    Object.entries(bakeFails.value).map(([name, f]) => ({ name, ...f }))
  ));

  /** 「缺 NOSR」那些行（中性提示，不是错误）。 */
  const bakeNoteList = computed(() => (
    Object.entries(bakeNotes.value).map(([name, why]) => ({ name, why }))
  ));

  /* ---------------- 写回 ---------------- */

  /** 生成要写回文档的全文（上半部分原文 + 下半部分终态行）。 */
  function output(): string {
    return list.value ? buildQcDoc(list.value, statuses.value) : '';
  }

  /** 把 output() 写回盘阵上那份 .txt（原地覆盖）。
   *
   *  目标**由用户粘路径指定**，不用文件选择器：浏览器的文件选择器只能选到用户本机
   *  的文件，而清单在盘阵上 —— 真机页面还是 http，那个 API 压根不存在（写盘走后端
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
      persist();          // 同时把用户刚粘的路径存下来，刷新后不用重粘
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
    // 一键解析
    bakeState, bakeTotal, bakeDone, bakeElapsed,
    bakeFails, bakeNotes, bakeFailList, bakeNoteList, bakeHead, bakeLine,
    rowForScene, bakeAll, stopBake,
  };
});
