/**
 * stores/queue.ts — 共享 SR 任务队列（阶段5，api-contract.md §3.3）
 * ------------------------------------------------------------------
 * 服务端唯一事实源：list/submit/cancel 走 REST；状态推进靠后台校准器广播
 * job_update（GET /api/queue/events SSE），前端仅按 task_id 归并覆盖 state。
 * 任务行状态机 = 进度（§3.3）：SUBMITTING → PENDING → RUNNING → COMPLETED/FAILED。
 * 掩码烘焙（查看器）→ setDraft() 预填队列表单（不自动提交，提交是真副作用）。
 */
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { loadSrConfig } from '../lib/scene.js';
import {
  apiListQueue, apiSubmitQueue, apiCancelQueue, subscribeQueueEvents,
} from '../lib/api.js';
import type { QueueTask, QueueSubmitBody, QueueSubmitResult, JobUpdateEvent } from '../lib/api.js';

/** 队列表单里的可调参数（「以这行参数再提交」整组带回）。 */
export type QueueTunables = Pick<
  QueueForm, 'sr_scale' | 'suffix' | 'gpu' | 'cloud_limit' | 'grid_align'
>;

/** 提交预填。两个来源：查看器场景（只带目录）、任务行「再提交」（带整组参数）。
    掩码**不进提交体** —— 它由后端按 <lq_path>/<输入影像 stem>_mask.tif 推导并
    校验存在性（最小原型 §4.3）；这里带的 mask_path 只用于表单显示，让用户看到
    后端认的是哪个文件（PAN.tif（RC）场景与目录名不同名）。 */
export interface QueueDraft {
  lq_path: string;
  /** 后端给的**权威**掩码路径（查看器场景来自 resolve/写掩码的响应，任务行来自
   *  该行自己的 params）。有它表单就显示它；没有才退回 derivedMaskPath 那份
   *  无 stat 的镜像 —— PAN.tif（RC）场景两者不同名，镜像会指错文件。 */
  mask_path?: string | null;
  /** 不带时其余字段取 defaultForm 的值。 */
  tunables?: Partial<QueueTunables>;
  /** 「再提交」来自哪一行（仅用于表单顶部提示）。 */
  taskId?: number;
  from?: 'viewer' | 'task';
}

/** 队列表单（QueuePage 编辑态；提交时剥掉未填项拼 body）。 */
export interface QueueForm {
  lq_path: string;
  sr_scale: number;
  suffix: string;
  gpu: number;
  cloud_limit: number;
  delete_ori: boolean;
  grid_align: boolean;
}

/* ================= 纯函数（vitest 可测） ================= */

/** run_sr 参数默认值（镜像 services/run_sr + tools/run_sr 缺省）。
    后缀留空、不在前端预填：空值由后端解析成 SR 团队配置里的 `<Suffix>`
    （backend/services/run_sr.py::default_suffix，如 260318），前端猜不出这个值。
    注意别改成「预填后端默认值」——预填值会作为显式参数发出去，按优先级反而
    压过配置文件，把一个可能过期的日期钉死在表单里。

    delete_ori 恒为 false：该开关原型期已禁用（backend/services/run_sr.py 的
    DELETE_ORI_MSG，传 true 会被 400 拒），表单里也没有对应控件。 */
export function defaultForm(): QueueForm {
  return {
    lq_path: '', sr_scale: 2, suffix: '',
    gpu: 0, cloud_limit: 80, delete_ori: false, grid_align: true,
  };
}

/** 场景带入的目录 / 任务行带回的参数 → 队列表单（缺省字段取默认值）。 */
export function draftToForm(d: QueueDraft): QueueForm {
  return { ...defaultForm(), lq_path: d.lq_path, ...(d.tunables ?? {}) };
}

/** 掩码推导规则的**无 stat 镜像**，只在拿不到权威值时兜底显示「将读哪个掩膜」。
    权威实现是 backend/services/scene_search.derived_mask_path（掩码名取**输入
    影像**的 stem），前端这里没有 stat 只能拿目录名顶替 —— `<目录名>.tif`（SC）
    场景两者相同，`PAN.tif`（RC）场景不同名。所以表单优先用后端给的
    `QueueDraft.mask_path`，这份镜像只用于用户手改目录后的即时反馈。
    它不回传后端，猜错也不影响提交。 */
export function derivedMaskPath(lqPath: string): string {
  const dir = normDir(lqPath.trim());
  const leaf = pathLeafOf(dir);
  return leaf ? dir + '/' + leaf + '_mask.tif' : '';
}

/** 队列表单 → POST /api/queue body（数值夹取；mask_path 恒 null 交后端推导）。
    后缀留空即由后端按 SR 配置文件里的 `<Suffix>` 决定，前端不替它决定。 */
export function formToSubmit(f: QueueForm): QueueSubmitBody {
  const body: QueueSubmitBody = {
    lq_path: f.lq_path.trim(),
    mask_path: null,
    sr_scale: clampInt(f.sr_scale, 1, 8, 2),
    suffix: f.suffix.trim(),
    gpu: clampInt(f.gpu, 0, 16, 0),
    cloud_limit: clampInt(f.cloud_limit, 0, 100, 80),
    delete_ori: f.delete_ori,
    grid_align: f.grid_align,
  };
  return body;
}

function clampInt(v: number, lo: number, hi: number, def: number): number {
  if (!Number.isFinite(v)) return def;
  return Math.min(hi, Math.max(lo, Math.round(v)));
}

/** 一行任务的耗时 = **本次运行**的时长：终态行取 finished_at − started_at，运行中
    用 nowSec 现算。两列都由后端在观测到真实转换点时钉下（RUNNING / 终态，见
    api/platform._task_state），所以量的是跑的时间，不含排队，也不是行的年龄。

    为什么不拿 created_at 当起点：一行 = 一个指纹，同一场景同一参数重交时幂等层
    复用同一行，created_at 停在**第一次**提交的时刻 —— 那样算出来的「耗时」是行龄，
    真机上表现为「30 时 00 分」（2026-09-18）。

    started_at 缺失（整段运行期间后端不在）或终态行缺 finished_at（升级前的老行）
    → null，界面显示「—」：宁可空着，不回落到 created_at 编一个数。 */
export interface TaskElapsed {
  seconds: number;
  running: boolean;
}

export function taskElapsed(t: QueueTask, nowSec: number): TaskElapsed | null {
  const start = Number(t.started_at);
  if (!Number.isFinite(start) || start <= 0) return null;
  const running = isActiveState(String(t.state));
  const end = running ? nowSec : Number(t.finished_at);
  if (!Number.isFinite(end) || end <= 0) return null;
  return { seconds: Math.max(0, Math.round(end - start)), running };
}

/** 未终结（还会自己推进）的状态。 */
export function isActiveState(state: string): boolean {
  return state === 'SUBMITTING' || state === 'PENDING' || state === 'RUNNING';
}

/** 秒 → 「1 时 02 分」/「3 分 12 秒」/「12 秒」。 */
export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h) return h + ' 时 ' + String(m).padStart(2, '0') + ' 分';
  if (m) return m + ' 分 ' + String(sec).padStart(2, '0') + ' 秒';
  return sec + ' 秒';
}

/** SSE job_update → 覆盖匹配 task 的 state（+ 后端给的话一并覆盖时间戳）；
    无匹配不动（权威在 list()）。

    时间戳必须跟着 state 一起落到本地，否则耗时会用上一次 GET 的快照算：本地那份
    通常就采于提交刚落库时（started_at/finished_at 还是 NULL），运行中靠 nowSec
    现算看不出来，一进终态就成了「—」（2026-09-17 的「0 秒」是同一个坑的前身）。
    后端只在自己**写库成功**时带这些字段 → 缺席即保留本地值，不回退成 undefined
    （时间戳丢掉整列变「—」，比旧值更糟）。 */
export function mergeJobUpdate(tasks: QueueTask[], ev: JobUpdateEvent): QueueTask[] {
  let hit = false;
  const next = tasks.map((t) => {
    if (t.task_id !== ev.task_id) return t;
    hit = true;
    const patch: Partial<QueueTask> = { state: ev.state };
    for (const col of ['updated_at', 'started_at', 'finished_at'] as const) {
      const v = Number(ev[col]);
      if (Number.isFinite(v) && v > 0) patch[col] = v;
    }
    return { ...t, ...patch };
  });
  return hit ? next : tasks;
}

/** 展示态 → badge 类别（表头标签/行内样式复用）。 */
export function stateTone(state: string): 'pending' | 'run' | 'ok' | 'fail' | 'muted' {
  if (state === 'SUBMITTING' || state === 'PENDING') return 'pending';
  if (state === 'RUNNING') return 'run';
  if (state === 'COMPLETED') return 'ok';
  if (state === 'FAILED') return 'fail';
  return 'muted';
}

/* ---------------- tasksForScene：任务区关联当前场景（阶段6） ----------------
   viewer 盘阵场景 ↔ /api/queue 行：lq_path == scene 父目录（run_sr 目录语义）且
   mask_path 基名 == <scene stem>_mask.tif。只命中「以该场景掩码发起的 SR 任务」，
   同目录下别家任务（不同 stem mask）与无掩码任务不误收。返回按 created_at 倒序。 */
export interface SceneTaskRef {
  lqPath: string | null;   // ViewerRec.lqPath（scene 文件父目录绝对路径）
  stem: string;            // scene 可读名（原图 stem，掩码名 <stem>_mask.tif 的前缀）
}

/** 去尾部分隔符（'/a/b/'、'\\a\\b\\' 归一，Windows 盘符 'C:' 不受影响）。 */
export function normDir(p: string): string {
  return p.replace(/[/\\]+$/, '');
}

/** 取路径基名（兼容 '/' 与 Windows '\\'；空 → ''）。 */
export function pathLeafOf(p: string): string {
  const parts = p.split(/[/\\]/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : '';
}

export function tasksForScene(tasks: QueueTask[], ref: SceneTaskRef): QueueTask[] {
  if (!ref.lqPath) return [];
  const dir = normDir(ref.lqPath);
  const wantMask = ref.stem + '_mask.tif';
  return tasks
    .filter((t) =>
      normDir(t.params.lq_path) === dir
      && t.params.mask_path !== null
      && pathLeafOf(t.params.mask_path) === wantMask)
    .sort((a, b) => b.created_at - a.created_at);
}

/* ================= store ================= */

export const useQueueStore = defineStore('queue', () => {
  const tasks = ref<QueueTask[]>([]);
  const connected = ref(false);
  const loading = ref(false);
  const error = ref('');
  const draft = ref<QueueDraft | null>(null);
  /** 阶段6 实时失败原因（task_id → job_update.error；仅运行期捕获，非契约字段，
      页面重载后旧 FAILED 行无原因可追 → 展示回退「见 log_dir」）。 */
  const failReason = ref<Record<number, string>>({});
  let _dispose: (() => void) | null = null;

  async function list(): Promise<void> {
    loading.value = true;
    error.value = '';
    try {
      const cfg = loadSrConfig();
      tasks.value = await apiListQueue(cfg);
    } catch (e) {
      error.value = '队列加载失败：' + (e instanceof Error ? e.message : String(e));
    } finally {
      loading.value = false;
    }
  }

  /** POST /api/queue；成功后刷新列表（idempotent RESUMED_* 也在任务行可见）。 */
  async function submit(body: QueueSubmitBody): Promise<QueueSubmitResult> {
    error.value = '';
    try {
      const cfg = loadSrConfig();
      const res = await apiSubmitQueue(cfg, body);
      draft.value = null;                    // 落库成功即清预填（含 RESUMED_*）
      await list();
      return res;
    } catch (e) {
      error.value = '提交失败：' + (e instanceof Error ? e.message : String(e));
      throw e;
    }
  }

  async function cancel(taskId: number): Promise<void> {
    error.value = '';
    try {
      const cfg = loadSrConfig();
      await apiCancelQueue(cfg, taskId);
      await list();
    } catch (e) {
      error.value = '取消失败：' + (e instanceof Error ? e.message : String(e));
    }
  }

  /** 订阅队列 SSE；页面 mount 时 connect、unmount 时 disconnect。 */
  function connect(): void {
    if (_dispose) return;
    const cfg = loadSrConfig();
    connected.value = true;
    _dispose = subscribeQueueEvents(
      cfg,
      (ev) => {
        if (ev.type !== 'job_update') return;
        tasks.value = mergeJobUpdate(tasks.value, ev);
        if (ev.state === 'FAILED' && ev.error) {
          failReason.value = { ...failReason.value, [ev.task_id]: ev.error };
        } else if (ev.state !== 'FAILED' && failReason.value[ev.task_id] !== undefined) {
          const next = { ...failReason.value };
          delete next[ev.task_id];
          failReason.value = next;
        }
      },
      () => { connected.value = false; },
    );
  }

  function disconnect(): void {
    if (_dispose) { _dispose(); _dispose = null; }
    connected.value = false;
  }

  return {
    tasks, connected, loading, error, draft, failReason,
    list, submit, cancel, connect, disconnect,
    /** 查看器「提交 SR」带过来的目录（不自动提交）。
        maskPath 传后端给的权威值（resolve 响应或写掩码响应里的；拿不到传 null
        让表单退回本地镜像推导）。 */
    setSrDraft(lqPath: string, maskPath?: string | null) {
      draft.value = { lq_path: lqPath, mask_path: maskPath ?? null };
    },
    /** 「以这行参数再提交」：整组参数填回表单，仍然要点提交才真的跑。
        掩码带该行自己的 params.mask_path 只为显示 —— 后端重提交时仍按
        `<lq_path>/<输入影像 stem>_mask.tif` 推导，同一目录会推导出同一个文件。

        suffix 带回来的可能是空串：改动前的旧行由 agent 工具写入、当时不做归一化。
        重提交时空串按新规则解析成配置文件里的值，与那一行自己的指纹对不上，
        于是新建任务而不是复用——只影响旧行，且产物名不同，属正确行为。 */
    setDraftFromTask(t: QueueTask) {
      draft.value = {
        lq_path: t.params.lq_path,
        mask_path: t.params.mask_path,
        taskId: t.task_id,
        from: 'task',
        tunables: {
          sr_scale: t.params.sr_scale, suffix: t.params.suffix,
          gpu: t.params.gpu, cloud_limit: t.params.cloud_limit,
          grid_align: t.params.grid_align,
        },
      };
    },
    setDraft(d: QueueDraft | null) { draft.value = d; },
    clearDraft() { draft.value = null; },
  };
});
