/**
 * 查看器状态（阶段2 骨架 → 阶段3 组件化时扩充为文件列表/多 rec/掩码状态）
 * ------------------------------------------------------------------
 * 阶段2 只承载「当前记录元信息 + 解码状态」；解码出的自然值 src/统计 st
 * 属页面瞬态（切换拉伸不重读文件），留在 ViewerPage 本地，不进 store。
 */
import { defineStore } from 'pinia';
import { ref } from 'vue';

/** 一条解码记录（元信息，对齐 tif-viewer.html 的 activeRec 最小集） */
export interface ViewRec {
  name: string;
  size: number;
  W: number;
  H: number;
  spp: number;
  bits: number;
  sampleFormat: number;
  photometric: number | null;
  compression: number | null;
  layout: string;
  /** 实际走的解码路由 */
  route: 'sparse' | 'chunked';
  sw: number;
  sh: number;
}

export const useViewerStore = defineStore('viewer', () => {
  const rec = ref<ViewRec | null>(null);
  const status = ref('');
  const error = ref('');
  const busy = ref(false);

  function setStatus(s: string) {
    status.value = s;
  }
  function setRec(r: ViewRec | null) {
    rec.value = r;
  }
  function fail(msg: string) {
    error.value = msg;
    status.value = '';
  }
  function reset() {
    rec.value = null;
    status.value = '';
    error.value = '';
    busy.value = false;
  }

  return { rec, status, error, busy, setStatus, setRec, fail, reset };
});
