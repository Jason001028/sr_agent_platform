<script setup lang="ts">
import { computed, ref } from 'vue';
import AppIcon from './AppIcon.vue';
import SceneSearchCandidate from './SceneSearchCandidate.vue';
const props = defineProps<{ data?: unknown; args?: Record<string, unknown>; detail?: string; status?: 'running' | 'completed' | 'failed' | 'unconfirmed' }>();
const emit = defineEmits<{ open: [item: Record<string, unknown>] }>();
const expanded = ref(false);
const result = computed(() => props.data && typeof props.data === 'object' ? props.data as Record<string, unknown> : {});
const rows = computed(() => Array.isArray(result.value.results) ? result.value.results.filter(r => r && typeof r === 'object') as Record<string, unknown>[] : []);
const total = computed(() => typeof result.value.count === 'number' ? result.value.count : rows.value.length);
const summary = computed(() => props.status === 'running' ? '正在检索匹配场景'
  : props.status === 'failed' ? (props.detail || '检索失败，请检查查询条件')
  : props.status === 'unconfirmed' ? '执行结果尚未确认'
  : total.value > rows.value.length ? `匹配 ${total.value} 个场景，返回前 ${rows.value.length} 个候选` : `匹配 ${total.value} 个场景，点击候选打开影像`);
</script>
<template>
  <section class="scene-results" :data-status="status">
    <header class="scene-results-head"><AppIcon name="search" :size="15" /><strong title="search_scenes">检索目标场景</strong><span class="scene-results-status">{{ status === 'running' ? '检索中' : status === 'failed' ? '失败' : status === 'unconfirmed' ? '待确认' : '已完成' }}</span><span v-if="status === 'completed'" class="scene-count">{{ total }} 个结果</span></header>
    <p class="scene-summary" :title="summary">{{ summary }}</p>
    <div v-if="rows.length" class="scene-list"><SceneSearchCandidate v-for="(row, i) in (expanded ? rows : rows.slice(0, 2))" :key="String(row.path || row.sceneId || row.id || row.name || i)" :row="row" @open="emit('open', $event)" /></div>
    <div v-else-if="status === 'completed'" class="scene-empty">未找到符合条件的场景</div>
    <footer class="scene-footer"><details class="scene-query"><summary>查询参数</summary><pre>{{ JSON.stringify(args ?? {}, null, 2) }}</pre></details><button v-if="rows.length > 2" type="button" class="scene-more" :aria-expanded="expanded" @click="expanded = !expanded">{{ expanded ? '收起结果' : `查看全部 ${rows.length} 个结果` }}</button></footer>
    <slot />
  </section>
</template>
<style scoped>
.scene-results { width: 100%; box-sizing: border-box; padding: 10px; border: 1px solid var(--tb-divider); border-radius: 9px; background: var(--surface); color: var(--tb-fg); }
.scene-results-head { display: flex; align-items: center; gap: 6px; min-width: 0; font-size: 11px; }
.scene-results-head strong { font-weight: 600; margin-right: auto; }
.scene-results-status { color: var(--tb-primary); font-size: 10px; white-space: nowrap; }
[data-status="failed"] .scene-results-status { color: var(--err); }
[data-status="unconfirmed"] .scene-results-status { color: var(--tb-muted); }
.scene-count { color: var(--tb-muted); font-size: 10px; white-space: nowrap; }
.scene-summary { margin: 5px 0 4px; color: var(--tb-muted); font-size: 10.5px; line-height: 16px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.scene-list { display: grid; gap: 2px; }
.scene-footer { display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 10px; padding-top: 5px; border-top: 1px solid var(--line); font-size: 10px; }
.scene-query { flex: 1; min-width: 0; color: var(--tb-muted); }
.scene-query summary { cursor: pointer; width: max-content; }
.scene-query pre { margin: 6px 0; max-height: 160px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; }
.scene-more { padding: 2px 0; border: 0; background: transparent; color: var(--tb-primary); font: inherit; cursor: pointer; }
.scene-more:hover { text-decoration: underline; }
.scene-more:focus-visible { outline: 2px solid var(--tb-focus); outline-offset: 2px; }
.scene-empty { padding: 8px 0; color: var(--tb-muted); font-size: 11px; }
</style>
