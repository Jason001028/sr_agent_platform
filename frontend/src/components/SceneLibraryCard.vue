<script setup lang="ts">
import { ref } from 'vue';
import type { SceneRow } from '../lib/scene.js';
import AppIcon from './AppIcon.vue';
import { tagGroups } from '../lib/sceneLibrary.js';

const props = defineProps<{ row: SceneRow; variants?: SceneRow[]; opening?: boolean }>();
const emit = defineEmits<{ open: []; remove: []; }>();
const expanded = ref(false);
const editing = ref(false);
const draftName = ref(props.row.name);
const displayName = ref(props.row.name);
const labelStorageKey = `sr.scene-card-label.${props.row.id}`;
try {
  const saved = window.localStorage.getItem(labelStorageKey);
  if (saved) displayName.value = saved;
} catch { /* localStorage 涓嶅彲鐢ㄦ椂浠嶅厑璁告湰娆＄紪杈?*/ }
function bytes(n: number): string { return n >= 1073741824 ? `${(n / 1073741824).toFixed(2)} GB` : n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${n} B`; }
function beginEdit(): void {
  draftName.value = displayName.value;
  editing.value = true;
}
function saveEdit(): void {
  const next = draftName.value.trim();
  if (!next) return;
  displayName.value = next;
  editing.value = false;
  try { window.localStorage.setItem(labelStorageKey, next); } catch { /* ignore storage failures */ }
}
function cancelEdit(): void {
  draftName.value = displayName.value;
  editing.value = false;
}
function dims(r: SceneRow): string { return r.W && r.H ? `${r.W} × ${r.H}` : '尺寸待解析'; }
</script>

<template>
  <article class="scene-card">
    <div class="scene-card-main">
      <div class="scene-cover"><img v-if="row.jpgUrl" :src="row.jpgUrl" alt="" loading="lazy" /><AppIcon v-else name="image" :size="24" /></div>
      <div class="scene-info">
        <div class="scene-eyebrow"><span>{{ row.satellite || '未知卫星' }}</span><i>·</i><span>{{ row.sensor || '未知传感器' }}</span><span v-if="row.date" class="scene-date">{{ row.date }}</span></div>
        <div v-if="editing" class="scene-name-edit">
          <input v-model="draftName" class="scene-name-input" aria-label="编辑场景显示名称" @keyup.enter="saveEdit" @keyup.esc="cancelEdit" />
          <button type="button" class="scene-name-save" @click="saveEdit">保存</button>
          <button type="button" class="scene-name-cancel" @click="cancelEdit">取消</button>
        </div>
        <h3 v-else :title="displayName">{{ displayName }}</h3>
        <div class="scene-facts"><span>{{ dims(row) }}</span><span>{{ bytes(row.size_bytes) }}</span><span :class="['scene-preview', row.hasPreview ? 'ready' : 'pending']">{{ row.hasPreview ? '预览已就绪' : '预览待生成' }}</span></div>
      </div>
      <button type="button" class="scene-open" :disabled="opening" @click="emit('open')">{{ opening ? '打开中…' : '打开' }}</button>
    </div>
    <div class="scene-card-foot">
      <span class="scene-id">场景详情</span>
      <div class="scene-card-actions">
        <button type="button" class="scene-action" aria-label="编辑场景显示名称" title="编辑场景显示名称" @click="beginEdit">
          <AppIcon name="settings" :size="16" label="编辑场景显示名称" />
        </button>
        <button type="button" class="scene-action scene-action-danger" aria-label="从场景库隐藏（本机）" title="从场景库隐藏（本机）" @click="emit('remove')">
          <AppIcon name="trash" :size="16" label="从场景库隐藏（本机）" />
        </button>
        <button type="button" class="scene-expand" :aria-expanded="expanded" :aria-label="expanded ? '收起更多信息' : '展开更多信息'" :title="expanded ? '收起更多信息' : '展开更多信息'" @click="expanded = !expanded"><AppIcon name="clipboard-list" :size="16" :label="expanded ? '收起详情' : '展开更多信息'" /></button>
      </div>
    </div>
    <div v-if="row.in_library" class="scene-tag-groups">
      <template v-for="group in tagGroups" :key="group.key">
        <div v-if="row.tags?.[group.key]?.length" class="scene-tag-row">
          <span>{{ group.label }}</span><b v-for="tag in row.tags[group.key]" :key="tag" :class="group.key">{{ tag }}</b>
        </div>
      </template>
      <span v-if="!tagGroups.some(g => row.tags?.[g.key]?.length)" class="no-tags">已入库 · 尚未设置标签</span>
    </div>
    <div v-if="expanded" class="scene-variants">
      <div class="scene-detail-grid"><span>完整场景 ID</span><strong>{{ row.name }}</strong><span>相对路径</span><strong>{{ row.rel || '未提供' }}</strong></div>
      <div v-if="variants?.length" class="scene-variant-title">阶段文件</div>
      <div v-for="variant in variants" :key="variant.id" class="scene-variant"><span class="variant-kind">{{ variant.id === row.id ? '输入本体' : '关联文件' }}</span><span>{{ dims(variant) }}</span><span>{{ bytes(variant.size_bytes) }}</span><span>{{ variant.hasPreview ? '预览就绪' : '未生成预览' }}</span></div>
    </div>
  </article>
</template>

<style scoped>
.scene-tag-groups { display: grid; gap: 7px; padding: 10px 14px; border-top: 1px solid #edf2ef; }
.scene-tag-row { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; font-size: 11px; }
.scene-tag-row > span, .no-tags { color: #78908a; font-size: 11px; margin-right: 5px; }
.scene-tag-row b { padding: 3px 8px; border-radius: 999px; font-weight: 500; background: #e5f1eb; color: #326b58; overflow-wrap: anywhere; }
.scene-tag-row b.content { background: #e7eef9; color: #526f9b; }.scene-tag-row b.quality { background: #fbefe1; color: #9a7142; }.scene-tag-row b.custom { background: #eee9f6; color: #7a659b; }
.scene-card { overflow: hidden; background: #fff; border: 1px solid #D8E3DF; border-radius: 12px; box-shadow: 0 1px 2px rgba(0,0,0,.04), 0 4px 12px rgba(0,0,0,.06), 0 12px 32px rgba(0,0,0,.06); transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease; }
.scene-card:hover { transform: translateY(-2px); border-color: #a7c8bf; box-shadow: 0 2px 4px rgba(0,0,0,.05), 0 8px 18px rgba(0,0,0,.08), 0 16px 36px rgba(0,0,0,.08); }
.scene-card-main { display: flex; align-items: center; gap: 14px; padding: 12px 14px; }
.scene-cover { width: 72px; height: 72px; display: grid; place-items: center; flex: none; overflow: hidden; background: #F1F4F2; border-radius: 9px; color: #78908A; }
.scene-cover img { width: 100%; height: 100%; object-fit: contain; }
.scene-info { min-width: 0; flex: 1; }
.scene-eyebrow { display: flex; gap: 6px; align-items: center; color: #166C67; font-size: 11px; font-weight: 650; }
.scene-eyebrow i { color: #9ab2ac; font-style: normal; }.scene-date { margin-left: 5px; color: #78908A; font-weight: 500; }
h3 { margin: 7px 0 9px; overflow: hidden; color: #213C38; font-size: 14px; line-height: 1.3; text-overflow: ellipsis; white-space: nowrap; }
.scene-name-edit { display: flex; align-items: center; gap: 4px; margin: 5px 0 7px; }
.scene-name-input { min-width: 0; flex: 1; height: 27px; padding: 0 7px; border: 1px solid #b8d1c9; border-radius: 6px; color: #213C38; background: #fff; font: inherit; font-size: 12px; outline: none; }
.scene-name-input:focus { border-color: #166C67; box-shadow: 0 0 0 2px rgba(22,108,103,.12); }
.scene-name-save, .scene-name-cancel { height: 27px; padding: 0 7px; border-radius: 6px; font: inherit; font-size: 11px; cursor: pointer; }
.scene-name-save { border: 1px solid #166C67; background: #166C67; color: #fff; }
.scene-name-cancel { border: 1px solid #D8E3DF; background: #fff; color: #48615D; }
.scene-facts { display: flex; gap: 9px; flex-wrap: wrap; color: #48615D; font-size: 11px; }.scene-preview.ready { color: #166C67; }.scene-preview.pending { color: #8a7560; }
.scene-open { flex: none; padding: 8px 14px; border: 0; border-radius: 7px; background: #166C67; color: white; font: inherit; font-size: 12px; cursor: pointer; box-shadow: 0 2px 6px rgba(22,108,103,.2); }.scene-open:disabled { opacity: .6; cursor: wait; }
.scene-card-foot { display: flex; justify-content: space-between; gap: 10px; align-items: center; min-height: 30px; padding: 4px 14px; border-top: 1px solid #edf2ef; background: #fbfdfc; }.scene-id { color: #78908A; font-size: 10px; }.scene-card-actions { display: flex; align-items: center; gap: 2px; }.scene-action, .scene-expand { display: grid; place-items: center; width: 22px; height: 22px; padding: 0; border: 1px solid transparent; border-radius: 6px; background: transparent; color: #78908A; cursor: pointer; opacity: .78; }.scene-action:hover, .scene-action:focus-visible, .scene-expand:hover, .scene-expand:focus-visible { color: #166C67; border-color: #D8E3DF; background: #F5F8F6; opacity: 1; outline: none; }.scene-action-danger:hover, .scene-action-danger:focus-visible { color: #b45858; border-color: #ecd2d2; background: #fff7f7; }.scene-variants { display: grid; gap: 6px; padding: 10px 14px 12px; background: #F5F8F6; }.scene-detail-grid { display: grid; grid-template-columns: 90px 1fr; gap: 5px 10px; color: #78908A; font-size: 10px; }.scene-detail-grid strong { overflow-wrap: anywhere; color: #48615D; font-weight: 500; }.scene-variant-title { margin-top: 3px; color: #213C38; font-size: 11px; font-weight: 650; }.scene-variant { display: grid; grid-template-columns: 1.4fr 1fr 1fr 1fr; gap: 8px; color: #48615D; font-size: 10px; }.variant-kind { color: #213C38; font-weight: 650; }
</style>

