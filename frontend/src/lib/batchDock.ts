/**
 * lib/batchDock.ts — 「批量智能修复」任务中枢的显隐
 * ------------------------------------------------------------------
 * 默认收起：右栏先让置顶的「质检 TXT 清单」占住首屏，任务中枢等用户在清单
 * 操作行里点了「智能修复」再展开并聚焦序列选择（Agent 移交同样走这里打开）。
 *
 * 状态放在模块单例：QcListPanel 与 BatchTaskDock 分属两棵组件子树，共享同一个
 * ref 比来回派发事件简单，也不会漏掉任何入口。
 */
import { ref } from 'vue';

export const batchDockOpen = ref(false);

export function openBatchDock(): void {
  batchDockOpen.value = true;
}

export function toggleBatchDock(): void {
  batchDockOpen.value = !batchDockOpen.value;
}

/** Existing QC entry reveals selection without submitting. */
export function openBatchSelection(): void {
  openBatchDock();
  window.dispatchEvent(new CustomEvent('batch-task-open', { detail: { section: 'draft' } }));
}

/** A request to land directly on the human quality review block.
 *
 *  The Agent card and any other entry publish one lightweight intent; the
 *  existing Batch Panel opens its quality section and the existing quality
 *  store selects the Item + first pending region.  No second review state is
 *  introduced.  The token makes identical repeated clicks observable. */
export interface BatchQualityRequest {
  token: number;
  itemId?: string;
  regionId?: string;
}
export const batchQualityRequest = ref<BatchQualityRequest | null>(null);
let qualityRequestToken = 0;

export function requestQualityReview(itemId?: string, regionId?: string): void {
  qualityRequestToken += 1;
  batchQualityRequest.value = { token: qualityRequestToken, itemId, regionId };
  openBatchDock();
  window.dispatchEvent(new CustomEvent('batch-task-open', {
    detail: { section: 'monitor', quality: true },
  }));
}
