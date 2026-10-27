<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue';
import AppIcon from './AppIcon.vue';
import { apiResolveScene, fetchExistingSceneJpg } from '../lib/api.js';
import { loadSrConfig } from '../lib/scene.js';
import type { SceneRow } from '../lib/scene.js';
const props = defineProps<{ row: Record<string, unknown> }>();
const emit = defineEmits<{ open: [row: Record<string, unknown>] }>();
const element = ref<HTMLElement | null>(null);
const src = ref('');
let observer: IntersectionObserver | null = null;
let disposed = false;
const controller = new AbortController();
function value(...keys: string[]): string {
  const v = keys.map(key => props.row[key]).find(v => v != null && v !== '');
  return v == null ? '' : String(v);
}
const name = value('name') || value('path').split(/[\\/]/).pop() || value('sceneId', 'id') || '未知场景';
const facts = [value('date'), value('satellite'), value('type', 'stage', 'sensor')].filter(Boolean).join(' · ') || '场景信息待补充';
function clearPreview(): void {
  if (src.value) URL.revokeObjectURL(src.value);
  src.value = '';
}
async function loadPreview(): Promise<void> {
  if (props.row.fake || disposed) return;
  try {
    const cfg = loadSrConfig();
    // Metadata resolution only; missing previews never call /preview or decode TIFF.
    const preview = value('previewUrl', 'preview_url');
    const scene = 'hasPreview' in props.row ? props.row as unknown as SceneRow
      : preview ? { ...props.row, id: value('id', 'sceneId', 'name'), jpgUrl: preview, hasPreview: true } as unknown as SceneRow
      : (await apiResolveScene(cfg, value('path') ? { path: value('path') } : { name },
          { signal: controller.signal })).row;
    if (disposed) return;
    const blob = await fetchExistingSceneJpg(cfg, scene);
    if (blob && !disposed) src.value = URL.createObjectURL(blob);
  } catch { /* Missing/unavailable previews remain quiet placeholders. */ }
}
onMounted(() => {
  if (typeof IntersectionObserver === 'undefined') { void loadPreview(); return; }
  observer = new IntersectionObserver(entries => {
    if (entries.some(entry => entry.isIntersecting)) { observer?.disconnect(); void loadPreview(); }
  });
  if (element.value) observer.observe(element.value);
});
onBeforeUnmount(() => { disposed = true; controller.abort(); observer?.disconnect(); clearPreview(); });
</script>
<template>
  <button ref="element" type="button" class="scene-row" :title="name" @click="emit('open', row)">
    <span class="scene-thumb"><img v-if="src" :src="src" alt="" loading="lazy" decoding="async" @error="clearPreview" /><AppIcon v-else name="image" :size="16" /></span>
    <span class="scene-meta"><strong>{{ name }}</strong><span :title="facts">{{ facts }}</span></span>
    <span class="scene-open" aria-hidden="true">↗</span>
  </button>
</template>
<style scoped>
.scene-row { display: flex; align-items: center; width: 100%; height: 62px; box-sizing: border-box; gap: 9px; padding: 6px; border: 1px solid transparent; border-radius: 7px; background: transparent; color: var(--tb-fg); text-align: left; font: inherit; cursor: pointer; transition: background var(--motion-hover), border-color var(--motion-hover); }
.scene-row:hover { background: var(--tb-hover); border-color: var(--tb-divider); }
.scene-row:focus-visible { outline: 2px solid var(--tb-focus); outline-offset: -2px; }
.scene-thumb { display: grid; place-items: center; flex: none; width: 38px; height: 38px; overflow: hidden; border-radius: 5px; background: var(--tb-bg); color: var(--ink-faint); }
.scene-thumb img { width: 100%; height: 100%; object-fit: cover; }
.scene-meta { display: grid; gap: 3px; min-width: 0; flex: 1; }
.scene-meta strong { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; overflow-wrap: anywhere; font-size: 11px; font-weight: 500; line-height: 14px; }
.scene-meta > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 10px; line-height: 13px; color: var(--tb-muted); }
.scene-open { flex: none; color: var(--tb-muted); font-size: 12px; opacity: 0; }
.scene-row:hover .scene-open, .scene-row:focus-visible .scene-open { opacity: 1; }
</style>
