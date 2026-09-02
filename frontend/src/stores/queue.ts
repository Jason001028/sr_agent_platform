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

/** 提交表单预填（/api/masks task_draft 同构；lq_path 为原图目录）。 */
export interface QueueDraft {
  lq_path: string;
  mask_path: string | null;
  sr_scale: number;
  suffix: string;
  gpu: number;
  cloud_limit: number;
  delete_ori: boolean;
  grid_align: boolean;
}

/** 队列表单（QueuePage 编辑态；提交时剥掉未填项拼 body）。 */
export interface QueueForm {
  lq_path: string;
  mask_path: string;
  sr_scale: number;
  suffix: string;
  gpu: number;
  cloud_limit: number;
  delete_ori: boolean;
  grid_align: boolean;
}

/* ================= 纯函数（vitest 可测） ================= */

/** run_sr 参数默认值（镜像 services/run_sr + tools/run_sr 缺省）。 */
export function defaultForm(): QueueForm {
  return {
    lq_path: '', mask_path: '', sr_scale: 2, suffix: '',
    gpu: 0, cloud_limit: 80, delete_ori: false, grid_align: true,
  };
}

/** 掩码 task_draft → 队列表单（'' 归一为空串，提交时转 null）。 */
export function draftToForm(d: QueueDraft): QueueForm {
  return {
    lq_path: d.lq_path, mask_path: d.mask_path ?? '',
    sr_scale: d.sr_scale, suffix: d.suffix, gpu: d.gpu,
    cloud_limit: d.cloud_limit, delete_ori: d.delete_ori, grid_align: d.grid_align,
  };
}

/** 队列表单 → POST /api/queue body（空 mask_path/空串后缀转 null；数值夹取）。 */
export function formToSubmit(f: QueueForm): QueueSubmitBody {
  const body: QueueSubmitBody = {
    lq_path: f.lq_path.trim(),
    mask_path: f.mask_path.trim() || null,
    sr_scale: clampInt(f.sr_scale, 1, 8, 2),
    suffix: f.suffix.trim() || '',
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

/* ================= store ================= */

export const useQueueStore = defineStore('queue', () => {
  const tasks = ref<QueueTask[]>([]);
  const connected = ref(false);
  const loading = ref(false);
  const error = ref('');
  const draft = ref<QueueDraft | null>(null);
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
        if (ev.type === 'job_update') {
          tasks.value = mergeJobUpdate(tasks.value, ev);
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
    tasks, connected, loading, error, draft,
    list, submit, cancel, connect, disconnect,
    setDraft(d: QueueDraft | null) { draft.value = d; },
    clearDraft() { draft.value = null; },
  };
});
