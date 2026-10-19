/**
 * stores/notices.ts — 右下角「任务提醒」栈（2026-09-22）
 * ------------------------------------------------------------------
 * 只做三件事：排队（带去重与封顶）、计时（成功条自动消失）、移除。
 * 「什么事件值得提醒、文案怎么写」全在 lib/notices.ts 的纯函数里。
 *
 * **全局单例，挂在 App 外壳上**（不是查看器页）：提醒的价值就在「人在别的页面干活
 * 时也能收到」，挂进任何单页都会随页面 unmount 一起消失。也正因为这样，离开查看器
 * 时不能清栈 —— 栈里那条提醒是用户在别的页面上唯一能看到的痕迹。
 *
 * 去重键是 `<task_id>:<state>`，**一次会话内有效**：SSE 重连后后端会补发真正变过的
 * 行（sr-api 重启后 task_cache 为空，会按库里存的状态重判一次），没有去重键的话同一
 * 条完成提醒会随着每次断线重连再弹一遍。刷新页面即清空 —— 刷新期间跑完的任务**不补
 * 弹**（用户口径 2026-09-22：回到队列页看列表即可，不引入跨会话状态）。
 */
import { defineStore } from 'pinia';
import { ref } from 'vue';
import type { JobNotice, JobNoticeDraft } from '../lib/notices.js';

/** 同屏最多几条：再多就压住影像了。超出的从**旧的**开始挤掉。 */
export const NOTICE_MAX = 4;
/** 成功条自动消失的时间。失败条不自动消失（`persist`）。 */
export const NOTICE_OK_MS = 8000;
/** 去重键表的封顶：一条键 10 字节上下，攒够这么多说明会话已经很长了，清一次即可。 */
const FIRED_MAX = 500;

export const useNoticesStore = defineStore('notices', () => {
  const items = ref<JobNotice[]>([]);
  /** 已经提醒过的业务键（见文件头「去重」）。 */
  const fired = ref<Set<string>>(new Set());
  const timers = new Map<number, ReturnType<typeof setTimeout>>();
  let seq = 1;

  function clearTimer(id: number): void {
    const t = timers.get(id);
    if (t !== undefined) { clearTimeout(t); timers.delete(id); }
  }

  /** 收下一条草稿。重复键、空草稿一律忽略。 */
  function push(draft: JobNoticeDraft | null): void {
    if (!draft) return;
    if (fired.value.has(draft.key)) return;
    if (fired.value.size >= FIRED_MAX) fired.value.clear();
    fired.value.add(draft.key);

    const item: JobNotice = { ...draft, id: seq++ };
    const next = [...items.value, item];
    // 挤掉旧的：连计时器一起收，否则被挤掉那条的定时器还会再调一次 dismiss。
    while (next.length > NOTICE_MAX) {
      const gone = next.shift()!;
      clearTimer(gone.id);
    }
    items.value = next;
    if (!item.persist) {
      timers.set(item.id, setTimeout(() => { dismiss(item.id); }, NOTICE_OK_MS));
    }
  }

  function dismiss(id: number): void {
    clearTimer(id);
    items.value = items.value.filter((n) => n.id !== id);
  }

  /** 清空（含计时器）。**去重键不清**：清空的是屏上的条，不是「这条已提醒过」。 */
  function clear(): void {
    for (const id of [...timers.keys()]) clearTimer(id);
    items.value = [];
  }

  /** 连去重键一起清 —— 只给单测用（模块级单例会在用例之间串味）。 */
  function reset(): void {
    clear();
    fired.value = new Set();
  }

  return { items, push, dismiss, clear, reset };
});
