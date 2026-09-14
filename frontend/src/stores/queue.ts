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

/** 从查看器场景带入的提交预填：只有原图目录。
    掩码不进表单 —— 它由后端按 <lq_path>/<目录名>_mask.tif 推导并校验存在性
    （最小原型 §4.3），前端只显示推导结果，不参与提交。 */
export interface QueueDraft {
  lq_path: string;
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
    后缀默认非空：空后缀会让 SR 的输出名等于输入名（契约 §2.4-1），
    后端另有 SR_SUFFIX_DEFAULT 兜底，这里预填同一约定值让操作员看得见。 */
export function defaultForm(): QueueForm {
  return {
    lq_path: '', sr_scale: 2, suffix: 'sr',
    gpu: 0, cloud_limit: 80, delete_ori: false, grid_align: true,
  };
}

/** 场景带入的目录 → 队列表单（其余字段取默认值）。 */
export function draftToForm(d: QueueDraft): QueueForm {
  return { ...defaultForm(), lq_path: d.lq_path };
}

/** 后端 §4.3 的掩码推导规则，仅供界面显示「将读哪个掩膜」——权威实现在
    backend/api/platform.py derived_mask_path，前端这一份不回传后端，猜错也不影响提交。 */
export function derivedMaskPath(lqPath: string): string {
  const dir = normDir(lqPath.trim());
  const leaf = pathLeafOf(dir);
  return leaf ? dir + '/' + leaf + '_mask.tif' : '';
}

/** 队列表单 → POST /api/queue body（数值夹取；mask_path 恒 null 交后端推导）。
    后缀留空即交给后端的 SR_SUFFIX_DEFAULT，前端不替它决定。 */
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

/** SSE job_update → 覆盖匹配 task 的 state；无匹配不动（权威在 list()）。 */
export function mergeJobUpdate(tasks: QueueTask[], ev: JobUpdateEvent): QueueTask[] {
  let hit = false;
  const next = tasks.map((t) => {
    if (t.task_id === ev.task_id) {
      hit = true;
      return { ...t, state: ev.state };
    }
    return t;
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
    /** 查看器「提交 SR」带过来的目录（不自动提交）。 */
    setSrDraft(lqPath: string) { draft.value = { lq_path: lqPath }; },
    setDraft(d: QueueDraft | null) { draft.value = d; },
    clearDraft() { draft.value = null; },
  };
});
