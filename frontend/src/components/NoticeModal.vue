<script setup lang="ts">
/**
 * NoticeModal.vue — 模态提示（不自动消失）
 * ------------------------------------------------------------------
 * 给「需要照着做」的说明用：内容常常是后端拼出来的一长串目录清单（试过哪几个
 * 候选、各缺什么），toast 六秒既看不完也留不住。
 *
 * 由 store 的 modal 状态驱动（showModal/hideModal）。挂在**页面根**上，不要塞进
 * TifCanvas 的 slot —— .stage 里有 transform 祖先，会把这个 fixed 定位困在里面。
 * Esc 或点「知道了」关闭；点遮罩也关。
 */
import { onMounted, onUnmounted } from 'vue';
import { useViewerStore } from '../stores/viewer';

const store = useViewerStore();

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape' && store.modal.visible) store.hideModal();
}
onMounted(() => window.addEventListener('keydown', onKey));
onUnmounted(() => window.removeEventListener('keydown', onKey));
</script>

<template>
  <div v-if="store.modal.visible" class="notice-modal" @click.self="store.hideModal()">
    <div class="nm-box" role="alertdialog" aria-modal="true">
      <div class="nm-title">{{ store.modal.title }}</div>
      <div class="nm-body">{{ store.modal.body }}</div>
      <div v-if="store.modal.hint" class="nm-hint">{{ store.modal.hint }}</div>
      <button class="nm-ok" type="button" @click="store.hideModal()">知道了</button>
    </div>
  </div>
</template>

<style scoped>
.notice-modal {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(35, 42, 40, 0.28);
  z-index: 100;
}
.nm-box {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-width: min(560px, 80vw);
  max-height: 70vh;
  overflow: auto;
  background: var(--surface);
  border: 1px solid rgba(236, 236, 240, 0.9);
  border-radius: var(--r-card);
  box-shadow: var(--shadow-pop);
  padding: 22px 26px;
}
.nm-title { color: var(--ink); font-size: 15px; font-weight: 600; }
/* 长路径不换行会把卡片撑破（后端那串候选清单里全是绝对路径）*/
.nm-body { color: var(--ink-sub); font-size: 13px; line-height: 1.7; white-space: pre-wrap; word-break: break-all; }
.nm-hint { color: var(--ink-sub); font-size: 12px; line-height: 1.6; opacity: 0.85; }
.nm-ok {
  align-self: flex-end;
  margin-top: 4px;
  padding: 6px 18px;
  background: var(--surface-2);
  border: 1px solid var(--ok-line);
  border-radius: 8px;
  color: var(--accent-deep);
  font-size: 13px;
  cursor: pointer;
}
.nm-ok:hover { background: var(--chrome); }
</style>
