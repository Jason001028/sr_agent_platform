/**
 * lib/notices.ts — 「任务跑完了」这条提醒的纯逻辑（2026-09-22）
 * ------------------------------------------------------------------
 * 为什么要有它：队列 SSE 以前只在**任务队列页**或**查看器侧舱开着**的时候才连，
 * 用户提交完切到查看器干活，任务跑完是没有任何提示的 —— 得自己回去翻列表。
 * 现在订阅改成应用级常驻（stores/queue.ts 的 connect 引用计数 + 断线重连），
 * 终态一到就在这里攒一条提醒，由 JobNoticeStack 弹在右下角。
 *
 * 本模块只有纯函数与类型（不碰 pinia、不碰 DOM），store 只管排队与计时。
 *
 * 两条刻意的口径：
 *  * **只认终态 COMPLETED / FAILED**。中间态（SUBMITTING/PENDING/RUNNING）不提醒 ——
 *    那是「还在跑」，而用户要的是「不用守着」。
 *  * **不猜产物名**。事件帧（job_update）里只有 task_id/state/时间戳，没有产物名；
 *    场景目录 + 倍率足够认出是哪一景哪一次，编一个可能对不上的文件名反而误导
 *    （产物名由 SR 团队的 <Suffix> 决定，前端拼不出来）。
 */
import type { JobUpdateEvent, QueueTask } from './api.js';

/** 右下角一条提醒。`id` 由 store 发（本模块不产生），`key` 是去重用的业务身份。 */
export interface JobNotice {
  id: number;
  /** `<task_id>:<state>` —— 同一条任务的同一个终态只提醒一次。 */
  key: string;
  taskId: number;
  kind: 'ok' | 'fail';
  title: string;
  /** 第二行：场景目录名 · 倍率（拿不到行时只有倍率或缺省）。 */
  text: string;
  /** 失败原因（完成时为空串）。 */
  reason: string;
  /** true = 不自动消失（失败条：原因只在运行期 SSE 里带得回来，调一眼就没了等于白提醒）。 */
  persist: boolean;
}

/** store 排队时用的草稿（`id` 由 store 补）。 */
export type JobNoticeDraft = Omit<JobNotice, 'id'>;

/** 末段路径（`/a/b/c` 与 `W:\a\b\c` 都给 `c`）。
 *
 *  与 `stores/queue.pathLeafOf` 同一条规则，但那边的模块会 import 本模块（推送提醒），
 *  反向 import 会成环，所以这里就地写一遍。两处规则都很短，且**都只用于显示**。 */
function leaf(p: string | null | undefined): string {
  const s = String(p ?? '').replace(/\\/g, '/').replace(/\/+$/, '');
  const parts = s.split('/').filter(Boolean);
  return parts.length ? parts[parts.length - 1] : '';
}

/** 这一帧值得提醒吗（终态 + 能认出是哪条任务）。 */
export function noticeKey(ev: JobUpdateEvent): string | null {
  const state = String(ev.state || '').toUpperCase();
  if (state !== 'COMPLETED' && state !== 'FAILED') return null;
  if (!Number.isFinite(Number(ev.task_id))) return null;
  return ev.task_id + ':' + state;
}

/** job_update 帧 + （可能已经不在列表里的）任务行 → 一条提醒草稿；不值一提则 null。
 *
 *  `task` 缺席是**正常情况**：订阅是应用级常驻的，帧可能在这次会话还没 GET 过队列、
 *  或那一行已被清出列表时到达。这时只报 task_id 与倍率位置留空，绝不编造目录名。
 *  `liveError` = 运行期捕获的失败原因（stores/queue 的 failReason），比行里的 log_dir 具体。 */
export function jobNotice(
  ev: JobUpdateEvent, task: QueueTask | null, liveError?: string | null,
): JobNoticeDraft | null {
  const key = noticeKey(ev);
  if (!key) return null;
  const state = String(ev.state).toUpperCase();
  const ok = state === 'COMPLETED';
  const dir = leaf(task?.params.lq_path);
  const parts: string[] = [];
  if (dir) parts.push(dir);
  if (task && Number.isFinite(Number(task.params.sr_scale))) {
    parts.push('×' + Number(task.params.sr_scale));
  }
  const reason = ok ? '' : (liveError || '')
    || (leaf(task?.log_dir) ? '原因见目录 ' + leaf(task?.log_dir) : '原因见队列页');
  return {
    key,
    taskId: ev.task_id,
    kind: ok ? 'ok' : 'fail',
    title: (ok ? '超分完成' : '超分失败') + ' #' + ev.task_id,
    text: parts.join(' · '),
    reason,
    persist: !ok,
  };
}
