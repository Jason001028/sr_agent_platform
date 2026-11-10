<script setup lang="ts">
/**
 * DecodeOverlay.vue — 解码/合并/生成掩码遮罩 + 进度条 + toast + 错误条
 * ------------------------------------------------------------------
 * tif-viewer.html #decodeMask/#toast/#errBox 直译。绝对定位于 stage 上（TifCanvas slot 注入）。
 * showMask/hideMask/updateProgressUI 由 store 编排。
 */
import { useViewerStore } from '../stores/viewer';
import { computed } from 'vue';

const store = useViewerStore();
const previewVisible = computed(() => {
  const rec = store.recs.find(r => r.id === store.imageLoad.recId);
  return store.imageLoad.active && !!(rec?.progressivePreview || (rec?.thumb && ['装载本地预览', '生成本地 JPG'].includes(store.imageLoad.stage)));
});
const progressLabel = computed(() => {
  if(store.imageLoad.active&&store.imageLoad.state==='paused')return '已暂停';
  if(store.imageLoad.active&&store.imageLoad.state==='pausing')return '正在暂停…';
  if(!store.overlay.indeterminate)return store.overlay.progress+'%';
  return store.imageLoad.stage.includes('服务端')?'等待服务端处理…':'处理中…';
});
</script>

<template>
  <!-- 解码/合并/生成遮罩 -->
  <Transition name="decode-fade">
    <div v-if="store.overlay.visible" class="decode-mask" :class="{ 'preview-ready': previewVisible }">
      <div class="decode-box">
        <div class="spinner"></div>
        <div class="mask-title">{{ previewVisible ? (store.imageLoad.stage === '生成本地 JPG' ? '影像已显示，后台生成 JPG' : '整图概览已显示，后台回填中') : store.overlay.title }}</div>
        <div v-if="store.overlay.sub && !previewVisible" class="mask-sub">{{ store.overlay.sub }}</div>
        <template v-if="store.imageLoad.active">
          <div class="mask-sub" aria-live="polite">{{ store.imageLoad.stage }}</div>
          <div v-if="store.imageLoad.state === 'pausing'" class="mask-sub">正在暂停，当前读取或处理块结束后生效…</div>
          <div v-if="store.imageLoad.state === 'paused'" class="mask-sub">已暂停，继续后保留当前进度</div>
          <div class="load-actions" @pointerdown.stop>
            <button v-if="store.imageLoad.state === 'running'" type="button" @click="store.pauseImageLoad">暂停</button>
            <button v-else-if="store.imageLoad.state !== 'stopped'" type="button" @click="store.resumeImageLoad">继续</button>
            <button type="button" :disabled="store.imageLoad.state === 'stopped'" @click="store.stopImageLoad">停止加载</button>
          </div>
        </template>
        <div v-if="store.overlay.bar" class="progress-wrap">
          <div class="progress-track" role="progressbar" aria-label="当前阶段处理进度" :aria-valuemin="0" :aria-valuemax="100"
            :aria-valuenow="store.overlay.indeterminate ? undefined : store.overlay.progress" :aria-valuetext="progressLabel">
            <div class="progress-bar" :class="{ indeterminate:store.overlay.indeterminate, paused:store.imageLoad.active&&store.imageLoad.state!=='running' }"
              :style="store.overlay.indeterminate ? undefined : { width: store.overlay.progress + '%' }"></div>
          </div>
          <div class="progress-pct" aria-live="polite">{{ progressLabel }}</div>
        </div>
      </div>
    </div>
  </Transition>

  <!-- toast -->
  <Transition name="notice-fade">
    <div v-if="store.toast" class="toast">{{ store.toast }}</div>
  </Transition>

  <!-- 错误条 -->
  <Transition name="notice-fade">
    <div v-if="store.error" class="err-box">{{ store.error }}</div>
  </Transition>
</template>

<style scoped>
.load-actions{display:flex;justify-content:center;gap:10px;margin:12px 0}.load-actions button{font:inherit;padding:6px 14px;border:1px solid var(--line);border-radius:7px;background:var(--surface);color:var(--ink);cursor:pointer}
.decode-mask.preview-ready { background: transparent; backdrop-filter: none; align-items: flex-end; justify-content: flex-end; padding: 16px; pointer-events: none; }
.preview-ready .decode-box { pointer-events: auto; gap: 5px; padding: 12px 16px; max-width: 360px; }
.preview-ready .spinner { display: none; }
/* 解码遮罩：浅色毛玻璃，浮于浅色画布之上；内容为白色卡片。
   **`user-select: none` 是必须的**：遮罩盖住整块画布，且是画布区里唯一带可选文字的一层。
   没有它时，在遮罩上按住拖动会先给标题/进度文字拉出一个文本选择（画布那边收不到 mousedown，
   平移一动不动）；此后**每次**在选中文字上按下拖动，Chrome 都按「拖选中内容」走原生
   drag-and-drop，页面收到 dragover → 分屏下落位提示「放在左侧 / 放在右侧」立刻亮起。
   看起来就是「在画面里拖一下就冒出换格提示」，与画布平移彻底撞在一起（2026-09-21 用户报）。
   注意这里只禁选中、**不放开点击**：生成掩码 / 合并期间画布不该收到 mousedown。 */
.decode-mask {
  position: absolute;
  inset: 0;
  user-select: none;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(233, 237, 235, 0.72);
  backdrop-filter: blur(4px);
  z-index: 20;
  text-align: center;
}
.decode-fade-enter-active,
.decode-fade-leave-active { transition: opacity var(--motion-fade) var(--ease-standard); }
.decode-fade-leave-active { pointer-events: none; }   /* 淡出期间别继续挡画布 */
.decode-fade-enter-from,
.decode-fade-leave-to { opacity: 0; }
.decode-fade-enter-active .decode-box { animation: decode-box-in var(--motion-fade) var(--ease-enter); }
@keyframes decode-box-in {
  from { opacity: 0; transform: translateY(4px) scale(0.99); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
.decode-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  max-width: 80%;
  background: var(--surface);
  border: 1px solid rgba(236, 236, 240, 0.9);
  border-radius: var(--r-card);
  box-shadow: var(--shadow-pop);
  padding: 26px 34px;
}
.spinner {
  width: 32px;
  height: 32px;
  border: 3px solid var(--surface-2);
  border-top-color: var(--accent-3);
  border-radius: 50%;
  animation: spin var(--motion-spin) var(--ease-linear) infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.mask-title { color: var(--ink); font-size: 15px; font-weight: 600; }
.mask-sub { color: var(--ink-sub); font-size: 12px; white-space: pre-wrap; word-break: break-all; }

.progress-wrap {
  width: 300px;
  display: flex;
  align-items: center;
  gap: 10px;
}
.progress-track {
  flex: 1;
  height: 6px;
  background: var(--surface-2);
  border-radius: 3px;
  overflow: hidden;
}
.progress-bar {
  height: 100%;
  background: var(--accent-grad);
  transition: width var(--motion-live);
}
.progress-pct { color: var(--ink-sub); font-size: 12px; min-width: 40px; text-align: right; }
.progress-bar.indeterminate { width:35%;animation:progress-pending 1.4s ease-in-out infinite; }
.progress-bar.paused { animation-play-state:paused; }
@keyframes progress-pending { from{transform:translateX(-100%)} to{transform:translateX(290%)} }
@media(prefers-reduced-motion:reduce){.progress-bar.indeterminate{animation:none;transform:translateX(90%);}}

/* toast：白色浮条 + 青绿语义色，替代原暗绿 */
.toast {
  position: absolute;
  left: 50%;
  bottom: 24px;
  transform: translateX(-50%);
  max-width: 70%;
  padding: 10px 16px;
  background: var(--surface);
  border: 1px solid var(--ok-line);
  border-radius: 10px;
  box-shadow: var(--shadow-pop);
  color: var(--accent-deep);
  font-size: 13px;
  z-index: 30;
  text-align: center;
}
.err-box {
  position: absolute;
  left: 50%;
  top: 12px;
  transform: translateX(-50%);
  max-width: 80%;
  padding: 8px 14px;
  background: var(--err-bg);
  border: 1px solid var(--err-line);
  border-radius: 10px;
  color: var(--err);
  font-size: 13px;
  z-index: 30;
  text-align: center;
  word-break: break-all;
}
.notice-fade-enter-active,
.notice-fade-leave-active { transition: opacity var(--motion-fade) var(--ease-standard), transform var(--motion-fade) var(--ease-standard); }
.notice-fade-enter-from,
.notice-fade-leave-to { opacity: 0; transform: translateX(-50%) translateY(4px); }
</style>
