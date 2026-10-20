<script setup lang="ts">
/**
 * ChatImagePicker.vue — 「从影像列表挑一张截进对话」的轻量浮层
 * ------------------------------------------------------------------
 * 两处聊天输入区共用。列的是 `viewer.recs`（含「一键解析」批量入列的懒加载卡），
 * 截图口径与卡片上那颗相机键完全一致（当前可见画面 → 见 viewer.captureForChat）。
 *
 * `viewer.recs` 只在内存里：在 /chat 页直接刷新后它是空的。这时**不报错**，
 * 只提示先回查看器打开影像 —— 这是路径（离开查看器 → 截图）本身的边界，
 * 不是故障。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useViewerStore } from '../stores/viewer';
import { useChatStore } from '../stores/chat.js';
import { useChatShot } from '../composables/useChatShot.js';
import { SHOT_MAX_COUNT } from '../lib/chatShot.js';
import FileThumb from './FileThumb.vue';

const emit = defineEmits<{ (e: 'close'): void }>();

const viewer = useViewerStore();
const chat = useChatStore();
const { busy: shotBusy, add: addShot } = useChatShot();
const note = ref('');

const full = computed(() => chat.pendingImages.length >= SHOT_MAX_COUNT);

function close(): void { emit('close'); }

function onKey(e: KeyboardEvent): void {
  if (e.key === 'Escape') close();
}
onMounted(() => window.addEventListener('keydown', onKey));
onBeforeUnmount(() => window.removeEventListener('keydown', onKey));

async function pick(recId: number): Promise<void> {
  const r = await addShot(recId);
  if (r.ok) { close(); return; }
  note.value = r.message;
}
</script>

<template>
  <Teleport to="body">
    <div class="pk-mask" data-e2e="chat-shot-picker" @click.self="close">
      <div class="pk" role="dialog" aria-modal="true" aria-label="选择影像截图">
        <div class="pk-head">
          <span class="pk-title">选择影像（截取当前可见画面）</span>
          <button type="button" class="pk-x" aria-label="关闭" @click="close">×</button>
        </div>

        <p v-if="!viewer.recs.length" class="pk-empty">
          影像列表是空的。截图取自查看器里正在看的画面，请先回
          <router-link class="pk-go" to="/viewer" @click="close">查看器</router-link>
          打开一张图，再回来添加。
        </p>

        <template v-else>
          <div class="pk-grid">
            <button v-for="rec in viewer.recs" :key="rec.id" type="button"
                    class="pk-item" data-e2e="chat-shot-item"
                    :disabled="full || shotBusy"
                    :title="rec.name"
                    @click="pick(rec.id)">
              <FileThumb :rec="rec" />
              <span class="pk-name">{{ rec.name }}</span>
            </button>
          </div>
          <p v-if="full" class="pk-note">已经有 {{ SHOT_MAX_COUNT }} 张了，删掉一张再加。</p>
        </template>

        <p v-if="note" class="pk-note err">{{ note }}</p>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.pk-mask {
  position: fixed; inset: 0; z-index: 60;
  display: flex; align-items: center; justify-content: center;
  padding: 24px;
  background: rgba(28, 34, 32, 0.32);
  animation: pk-fade 0.12s ease-out;
}
.pk {
  width: min(560px, 100%);
  max-height: min(520px, calc(100vh - 48px));
  display: flex; flex-direction: column;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-panel);
  box-shadow: var(--shadow-card);
  overflow: hidden;
}
.pk-head {
  flex: none; display: flex; align-items: center; justify-content: space-between;
  gap: 10px; padding: 10px 12px 8px; border-bottom: 1px solid var(--line);
}
.pk-title { font-size: 13px; font-weight: 600; color: var(--ink); }
.pk-x {
  border: none; background: transparent; color: var(--ink-faint);
  font-size: 17px; line-height: 1; padding: 0 5px; border-radius: 5px;
  cursor: pointer; font-family: inherit;
}
.pk-x:hover { color: var(--ink); background: var(--surface-2); }
.pk-grid {
  flex: 1; min-height: 0; overflow: auto;
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px; padding: 10px 12px 12px;
  align-content: start;
}
.pk-item {
  display: flex; flex-direction: column; gap: 4px;
  padding: 6px; border: 1px solid var(--line); border-radius: var(--r-ctrl);
  background: var(--surface); text-align: left; font-family: inherit;
  cursor: pointer; overflow: hidden;
  transition: border-color 0.15s ease, background 0.15s ease;
}
.pk-item:hover:not(:disabled) { border-color: var(--accent-2); background: var(--accent-soft); }
.pk-item:disabled { opacity: .5; cursor: not-allowed; }
.pk-name {
  font-size: 11px; color: var(--ink-sub); word-break: break-all;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden;
}
.pk-empty { margin: 0; padding: 16px 14px 18px; font-size: 12.5px; line-height: 1.8; color: var(--ink-sub); }
.pk-go { color: var(--accent-ink); }
.pk-note {
  flex: none; margin: 0; padding: 0 12px 10px;
  font-size: 11.5px; color: var(--ink-sub);
}
.pk-note.err { color: var(--err); }

@keyframes pk-fade { from { opacity: 0; } to { opacity: 1; } }
@media (prefers-reduced-motion: reduce) {
  .pk-mask { animation: none; }
}
</style>
