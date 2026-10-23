<script setup lang="ts">
import { computed, ref } from 'vue';
import AppIcon from './AppIcon.vue';

const props = defineProps<{ data?: unknown; args?: Record<string, unknown>; status?: 'running'|'completed'|'failed' }>();
const emit = defineEmits<{ open: [item: Record<string, unknown>] }>();
const expanded = ref(false);
const rows = computed(() => {
  const d = props.data && typeof props.data === 'object' ? props.data as Record<string, unknown> : {};
  return Array.isArray(d.results) ? d.results as Record<string, unknown>[] : [];
});
const total = computed(() => {
  const d = props.data && typeof props.data === 'object' ? props.data as Record<string, unknown> : {};
  return typeof d.count === 'number' ? d.count : rows.value.length;
});
function val(row: Record<string, unknown>, ...keys: string[]): string {
  const x = keys.map(k => row[k]).find(v => v != null && v !== '');
  return x == null ? '' : String(x);
}
</script>

<template>
  <section class="scene-results">
    <div class="scene-results-head"><span class="scene-results-title"><AppIcon name="search" :size="16" />检索场景</span><span class="scene-results-status">{{ status === 'running' ? '检索中' : status === 'failed' ? '检索失败' : `已完成 · ${total} 个结果` }}</span></div>
    <div v-if="!rows.length" class="scene-empty">{{ status === 'failed' ? '检索失败，请展开查看原因' : '未找到符合条件的场景' }}</div>
    <div v-else class="scene-list">
      <button v-for="row in (expanded ? rows : rows.slice(0, 3))" :key="val(row, 'sceneId', 'id', 'name')" type="button" class="scene-row" @click="emit('open', row)">
        <span class="scene-thumb"><img v-if="val(row, 'previewUrl', 'preview_url')" :src="val(row, 'previewUrl', 'preview_url')" alt="" /><AppIcon v-else name="image" :size="18" /></span>
        <span class="scene-meta"><strong :title="val(row, 'sceneId', 'id', 'name')">{{ val(row, 'sceneId', 'id', 'name') || '未知场景' }}</strong><span>{{ [val(row, 'date'), val(row, 'satellite'), val(row, 'sensor'), val(row, 'stage')].filter(Boolean).join(' · ') }}</span></span>
      </button>
    </div>
    <button v-if="rows.length > 3" type="button" class="scene-more" @click="expanded = !expanded">{{ expanded ? '收起结果' : `查看全部 ${total} 个结果` }}</button>
  </section>
</template>

<style scoped>
.scene-results { margin-top: 9px; padding: 10px; background: #F5F8F6; border: 1px solid #D8E3DF; border-radius: 8px; }
.scene-results-head, .scene-row { display: flex; align-items: center; }
.scene-results-head { justify-content: space-between; gap: 8px; color: #48615D; font-size: 11px; }
.scene-results-title { display: inline-flex; align-items: center; gap: 6px; color: #213C38; font-weight: 650; }
.scene-results-status { color: #166C67; }
.scene-list { display: grid; gap: 5px; margin-top: 8px; }
.scene-row { width: 100%; gap: 9px; padding: 5px; border: 0; border-radius: 6px; background: #fff; color: #213C38; text-align: left; cursor: pointer; }
.scene-row:hover { box-shadow: 0 0 0 1px #9bc5bc; }
.scene-thumb { width: 46px; height: 46px; display: grid; place-items: center; flex: none; overflow: hidden; background: #F1F4F2; color: #78908A; }
.scene-thumb img { width: 100%; height: 100%; object-fit: contain; }
.scene-meta { min-width: 0; display: grid; gap: 3px; }
.scene-meta strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; }
.scene-meta span { color: #48615D; font-size: 10px; }
.scene-more { margin-top: 8px; border: 0; background: transparent; color: #166C67; font-size: 11px; cursor: pointer; }
.scene-empty { padding: 12px 2px 3px; color: #48615D; font-size: 11px; }
</style>
