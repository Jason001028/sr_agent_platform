<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from 'vue';
import { useBatchPlaybackStore } from '../stores/batchPlayback';
import { useBatchGoalStore } from '../stores/batchGoal';
import { useViewerStore } from '../stores/viewer';
import { candidateScreenPoints, overlayMatchesImage } from '../lib/batchOverlay';
import { imageRef } from '../lib/spatial';

const playback = useBatchPlaybackStore();
const batch = useBatchGoalStore();
const viewer = useViewerStore();
const panes = computed(() => {
  const doc = playback.doc;
  if (!doc) return [];
  return viewer.panes.filter(p => p.rec?.thumb && overlayMatchesImage(doc, imageRef(p.rec))).map(p => ({
    side: p.side, rect: p.rect,
    regions: doc.candidates.slice(0, playback.regionIndex+1).map((candidate, index) => {
      const points = candidateScreenPoints(candidate, imageRef(p.rec!)!, p.rec!.thumb!.width,
        p.rec!.thumb!.height, p.view, { x: 0, y: 0, w: p.rect.w, h: p.rect.h });
      return { id: candidate.candidate_id, points: points?.map(p=>p.join(',')).join(' ') ?? '',
        drawing: index === playback.regionIndex && playback.phase === 'drawing' };
    }),
  }));
});

function toggle() {
  if (playback.phase === 'done') playback.reset();
  batch.setFollow(!batch.follow);
}
function escape(e: KeyboardEvent) { if (e.key === 'Escape' && playback.running) batch.setFollow(false); }
function visibility() { if (document.hidden && playback.running) batch.setFollow(false); }
watch(() => batch.overlayCloseSeq, () => {
  playback.hideOverlay();
  batch.setFollow(false);
}, { flush: 'sync' });
watch(() => [batch.follow, batch.goalId, viewer.drawMode] as const, ([follow, goal, drawing], previous) => {
  if (previous && goal !== previous[1]) playback.reset();
  if (follow && goal && !drawing && playback.mounted) void playback.start();
  else playback.stop();
});
onMounted(() => {
  playback.mounted = true; batch.playbackManaged = true;
  if (batch.follow) void playback.start();
  document.addEventListener('keydown', escape);
  document.addEventListener('visibilitychange', visibility);
});
onBeforeUnmount(() => {
  playback.stop(); playback.mounted = false; batch.playbackManaged = false;
  document.removeEventListener('keydown', escape);
  document.removeEventListener('visibilitychange', visibility);
});
</script>

<template>
  <div v-if="batch.goalId" class="repair-playback" data-e2e="repair-playback">
    <template v-if="playback.running || playback.phase === 'paused' || playback.phase === 'done'">
      <svg v-for="pane in panes" :key="pane.side" class="repair-vectors"
        :style="{ left: `${pane.rect.x}px`, top: `${pane.rect.y}px`, width: `${pane.rect.w}px`, height: `${pane.rect.h}px` }">
        <polygon v-for="region in pane.regions" :key="region.id" :points="region.points"
          pathLength="1" :class="{ tracing: region.drawing }"
          :style="{ '--motion-repair-trace': `${playback.duration}ms` }" />
      </svg>
    </template>
    <div class="repair-card" role="status" aria-live="polite" @mousedown.stop @dblclick.stop @wheel.stop>
      <div class="repair-heading">
        <span class="repair-dot" :class="{ live: playback.running }" />
        <strong>{{ playback.label }}</strong>
        <span v-if="playback.doc?.provenance === 'mock'" class="repair-mock">演示 · 非真实结果</span>
        <span v-if="playback.ordinal" class="repair-count">第 {{ playback.ordinal }} 景</span>
        <button type="button" class="repair-toggle" @click="toggle">{{ batch.follow ? '暂停' : playback.phase === 'paused' ? '继续' : '逐景展示' }}</button>
        <button v-if="playback.running" type="button" class="repair-skip" @click="playback.skip">跳过</button>
      </div>
      <div v-if="playback.name" class="repair-name" :title="playback.name">{{ playback.name }}</div>
      <div class="repair-steps" aria-hidden="true">
        <span :class="{ current: playback.phase === 'loading' }">加载定位</span><i />
        <span :class="{ current: playback.phase === 'detecting' }">识别区域</span><i />
        <span :class="{ current: playback.phase === 'drawing' }">绘制掩码</span>
      </div>
      <p v-if="playback.note">{{ playback.note }}</p>
      <small v-else>预加载下一景 · 每景依次展示 · 手动操作即暂停</small>
    </div>
  </div>
</template>

<style scoped>
.repair-playback{position:absolute;inset:0;z-index:7;pointer-events:none;overflow:hidden}
.repair-mock{font-size:10px;color:#f2ce8d}
.repair-vectors{position:absolute;overflow:hidden}
polygon{fill:#73cbb526;stroke:#86e5cb;stroke-width:2;vector-effect:non-scaling-stroke;stroke-linejoin:round;filter:drop-shadow(0 1px 3px #123c3980)}
polygon.tracing{stroke-dasharray:1;animation:repair-trace var(--motion-repair-trace) var(--ease-enter) both}
.repair-card{position:absolute;bottom:20px;left:50%;transform:translateX(-50%);width:min(440px,calc(100% - 32px));box-sizing:border-box;padding:14px 17px;border:1px solid #c5e3d638;border-radius:15px;background:linear-gradient(125deg,#183632ed,#263b38ed);box-shadow:0 8px 30px #071b2529;color:#e7f3ee;backdrop-filter:blur(16px);pointer-events:auto;font-size:12px}
.repair-heading{display:flex;align-items:center;gap:8px}.repair-heading strong{font-size:12px;font-weight:600}.repair-dot{width:6px;height:6px;background:#91aaa0;border-radius:50%;flex-shrink:0}.repair-dot.live{background:#90e3c2;box-shadow:0 0 0 4px #90e3c214}.repair-count{font-size:10px;color:#b1c9bf}.repair-toggle,.repair-skip{border:1px solid #c5e3d62b;border-radius:7px;padding:4px 10px;background:#d4f1e612;color:#e2f3ec;font:inherit;cursor:pointer}.repair-toggle{margin-left:auto}.repair-skip{border-color:transparent;background:transparent;color:#acc7bb}.repair-toggle:hover,.repair-skip:hover{background:#d4f1e626}.repair-name{margin-top:9px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:10px;color:#b5cbc1}.repair-steps{display:flex;align-items:center;gap:8px;margin:11px 0 9px;font-size:10px;color:#8da99d}.repair-steps i{height:1px;flex:1;background:#c5e3d623}.repair-steps .current{color:#a2efd0}.repair-card p,.repair-card small{display:block;margin:0;font-size:10px;line-height:1.6;color:#b5cbc1}
@keyframes repair-trace{0%{stroke-dashoffset:1;fill:#73cbb500}80%{stroke-dashoffset:0;fill:#73cbb500}100%{stroke-dashoffset:0;fill:#73cbb526}}

</style>
