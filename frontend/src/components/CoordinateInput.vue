<script setup lang="ts">
import { computed, ref, watch, onMounted, onBeforeUnmount } from 'vue';
import { parseLocPair } from '../lib/viewMath';
import { claimCoordinateHelp, coordinateHelpHidden, hideCoordinateHelp } from '../lib/coordinateFeedback';
const props = defineProps<{ sceneKey: string | number | null; sceneGroup?: number | null; coordinates: readonly [number, number] | null; sourceName: string; batchState: string; importRevision?: number }>();
const emit = defineEmits<{ locate: [x: string, y: string]; error: [message: string] }>();
const text = ref('');
const origin = ref<'external' | 'none' | 'manual'>('none');
const visible = ref(false);
const animated = ref(false), suppressed = ref(coordinateHelpHidden());
const animationKey = ref('');
let animationTimer: ReturnType<typeof setTimeout> | undefined;
let motion: MediaQueryList | undefined;
const base = `${import.meta.env.BASE_URL}icon/`;
function stopAnimation() { clearTimeout(animationTimer); animated.value = false; }
function preference() { if (motion?.matches) stopAnimation(); }
onMounted(() => { motion = window.matchMedia?.('(prefers-reduced-motion: reduce)'); motion?.addEventListener('change', preference); });
let timer: ReturnType<typeof setTimeout> | undefined;
function close() { visible.value = false; clearTimeout(timer); }
function show() { close(); visible.value = true; timer = setTimeout(close, 4500); }
const validExternal = computed(() => props.sceneKey !== null && props.coordinates?.length === 2 && props.coordinates.every(Number.isFinite) ? props.coordinates : null);
const badge = computed(() => ({ external: '已导入', none: '未提供', manual: '手动' })[origin.value]);
const heading = computed(() => ({ external: '外部坐标已载入', none: '当前未导入坐标', manual: '手动输入' })[origin.value]);
const explanation = computed(() => origin.value === 'external'
  ? `来源：${props.sourceName || '待修复清单'}的已解析行列号。按原顺序用于像素定位，不代表经纬度；地理坐标系未确认。`
  : origin.value === 'manual' ? '来源：手动输入，按像素坐标 X, Y 定位；不代表外部导入或经纬度。'
    : '当前场景未提供可确认的外部坐标。可手动输入像素坐标 X, Y；地理坐标系未确认。');
watch([() => props.sceneKey, () => props.coordinates, () => props.sourceName], () => {
  const pair = validExternal.value;
  text.value = pair ? `${pair[0]},${pair[1]}` : '';
  origin.value = pair ? 'external' : 'none';
  close();
  stopAnimation();
}, { immediate: true });
let sequence = 0;
watch([() => props.importRevision, () => props.sceneGroup], ([revision, group], [previousRevision, previousGroup]) => {
  const imported = !!revision && revision !== previousRevision;
  const changedGroup = group != null && group !== previousGroup;
  if ((!imported && !changedGroup) || !validExternal.value) return;
  text.value = `${validExternal.value[0]},${validExternal.value[1]}`;
  origin.value = 'external';
  stopAnimation();
  animationKey.value = `${Date.now()}-${++sequence}`;
  if (!motion?.matches) animated.value = true;
  if (claimCoordinateHelp()) show();
});
function animationLoaded(event: Event) {
  if (!(event.currentTarget as HTMLImageElement).src.includes('coordinate-lockin-animated.svg')) return;
  clearTimeout(animationTimer);
  if (animated.value) animationTimer = setTimeout(stopAnimation, 800);
}
function suppress(event: Event) {
  suppressed.value = (event.target as HTMLInputElement).checked;
  hideCoordinateHelp(suppressed.value);
}
function input(event: Event) {
  text.value = (event.target as HTMLInputElement).value;
  origin.value = text.value.trim() ? 'manual' : 'none';
  close(); stopAnimation();
}
function locate() {
  const pair = parseLocPair(text.value);
  if (!pair) { emit('error', '请输入有效的像素坐标 X, Y'); return; }
  text.value = `${pair[0]},${pair[1]}`;
  emit('locate', pair[0], pair[1]);
}
onBeforeUnmount(() => { close(); stopAnimation(); motion?.removeEventListener('change', preference); });
</script>
<template>
  <span class="coordinate-control">
    <input :value="text" aria-label="像素坐标 X, Y" type="text" inputmode="decimal" placeholder="输入坐标 X, Y" @input="input" @keydown.enter="locate" />
    <span class="coordinate-badge" :class="origin" :title="explanation" tabindex="0" :aria-label="badge + '：' + explanation">
      <span class="coordinate-lockin" aria-hidden="true"><img v-if="origin === 'external'" :key="animated ? animationKey : 'static'" :src="base + (animated ? `coordinate-lockin-animated.svg?event=${animationKey}` : 'coordinate-lockin-static.svg')" width="22" height="22" alt="" @load="animationLoaded" @error="stopAnimation" /><span v-else class="coordinate-info"></span></span>{{ badge }}
    </span>
    <button type="button" class="coordinate-locate" :disabled="!text.trim()" @click="locate">定位</button>
    <Transition name="coordinate-source">
      <span v-if="visible" class="coordinate-card" role="status" aria-live="polite">
        <span class="coordinate-icon" :class="origin" aria-hidden="true">{{ origin === 'external' ? '⌖' : 'ⓘ' }}</span>
        <span><strong>{{ heading }}</strong><small class="coordinate-source-name" :title="sourceName">{{ sourceName || '待修复清单' }} · 像素坐标</small></span>
        <label class="coordinate-suppress"><input type="checkbox" :checked="suppressed" @change="suppress" />不再显示</label>
        <button type="button" class="coordinate-close" aria-label="关闭坐标来源提示" @click="close">×</button>
      </span>
    </Transition>
  </span>
</template>
<style scoped>
.coordinate-control { position: relative; display: inline-flex; align-items: center; gap: 6px; flex: none; font-size: 12px; }
.coordinate-control input { width: 164px; height: 32px; box-sizing: border-box; padding: 0 9px; background: var(--tb-surface); color: var(--tb-fg); border: 1px solid var(--tb-border); border-radius: 8px; font: inherit; }
.coordinate-control input::placeholder { color: var(--tb-muted); opacity: .85; }
.coordinate-control input:focus-visible { outline: 2px solid var(--tb-focus); outline-offset: 1px; }
.coordinate-badge { display: inline-flex; align-items: center; gap: 3px; color: var(--ink-sub); font-size: 11px; white-space: nowrap; cursor: help; }
.coordinate-lockin { display: inline-grid; place-items: center; width: 22px; height: 22px; flex: none; }
.coordinate-lockin img { width: 22px; height: 22px; display: block; }
.coordinate-info::before { content: 'i'; display: grid; place-items: center; width: 13px; height: 13px; border: 1px solid currentColor; border-radius: 50%; font-size: 10px; }
.coordinate-suppress { display: flex; align-items: center; gap: 4px; color: var(--ink-sub); font-size: 11px; font-weight: 400; }
.coordinate-suppress input { width: 12px; height: 12px; padding: 0; margin: 0; accent-color: var(--tb-primary); }
.coordinate-badge.external, .coordinate-icon.external { color: var(--tb-primary); }
.coordinate-locate { height: 32px; border: 1px solid var(--tb-border); border-radius: 8px; padding: 0 10px; background: var(--tb-surface); color: var(--tb-fg); font: inherit; cursor: pointer; }
.coordinate-locate:disabled { opacity: .5; cursor: default; }
.coordinate-card { position: absolute; top: calc(100% + 8px); left: 0; width: 230px; max-width: calc(100vw - 24px); box-sizing: border-box; display: grid; grid-template-columns: 18px 1fr 16px; align-items: center; gap: 8px; padding: 10px 12px; z-index: 30; background: var(--tb-bg); color: var(--ink); border: 1px solid var(--tb-border); border-radius: 10px; box-shadow: 0 2px 5px #163e3110, 0 6px 18px #163e3114; white-space: normal; }
.coordinate-suppress { grid-area: 2 / 2; }
.coordinate-close { grid-area: 1 / 3; }
.coordinate-card strong { display: block; font-size: 12px; font-weight: 600; }
.coordinate-source-name { display: block; max-width: 160px; margin-top: 3px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink-sub); font-size: 10px; }
.coordinate-icon { color: var(--ink-sub); font-size: 17px; line-height: 1; }
.coordinate-close { border: 0; background: none; color: var(--ink-sub); padding: 0 2px; cursor: pointer; font-size: 16px; margin-left: auto; }
.coordinate-source-enter-active { transition: opacity var(--motion-live) var(--ease-standard); }
.coordinate-source-enter-from { opacity: 0; }
@media (prefers-reduced-motion: reduce) { .coordinate-source-enter-active { transition: none; } }
</style>
