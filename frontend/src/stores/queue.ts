/**
 * 共享任务队列状态（阶段5 实现）
 * ------------------------------------------------------------------
 * 设计约束（current-question §3.1）：多人共享同一队列，服务端为唯一事实源；
 * 前端乐观更新 + 并发处理 + SSE 推送（REST 动作，SSE 状态）。阶段5 先写契约文档再写代码。
 */
import { defineStore } from 'pinia';
import { ref } from 'vue';

export interface QueueJob {
  id: string;
  status: string;
  progress: number;
}

export const useQueueStore = defineStore('queue', () => {
  const jobs = ref<QueueJob[]>([]);
  const connected = ref(false);

  return { jobs, connected };
});
