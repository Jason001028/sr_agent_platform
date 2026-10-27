<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue';
import type { ViewerRec } from '../stores/viewer.js';
import { useScenesStore } from '../stores/scenes.js';
import { addToSceneLibrary, emptyTags, tagGroups } from '../lib/sceneLibrary.js';
import type { TagCategory } from '../lib/sceneLibrary.js';
import AppIcon from './AppIcon.vue';

const props = defineProps<{ records: ViewerRec[] }>();
const emit = defineEmits<{ close: [] }>();
const dialog = ref<HTMLDialogElement>();
const tags = ref(emptyTags());
const custom = ref('');
const saving = ref(false);
const error = ref('');
const saved = ref<number | null>(null);
const eligible = computed(() => props.records.filter(r => !!r.sceneId && !r.pending && !r.pendingError));
const skipped = computed(() => props.records.length - eligible.value.length);
function onKey(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    event.preventDefault();
    event.stopImmediatePropagation();
    close();
  }
}
onMounted(() => {
  dialog.value?.showModal();
  window.addEventListener('keydown', onKey, true);
});
onBeforeUnmount(() => window.removeEventListener('keydown', onKey, true));
function toggle(category: TagCategory, value: string): void {
  const values = tags.value[category];
  tags.value[category] = values.includes(value) ? values.filter(v => v !== value) : [...values, value];
}
function addCustom(): void {
  const value = custom.value.trim();
  if (!value) return;
  if (value.length > 40 || tags.value.custom.length >= 40) { error.value = '每个标签最多 40 字，每组最多 40 个。'; return; }
  if (!tags.value.custom.includes(value)) tags.value.custom.push(value);
  custom.value = '';
  error.value = '';
}
function close(): void { if (!saving.value) emit('close'); }
async function save(): Promise<void> {
  error.value = '';
  addCustom();
  if (error.value) return;
  saving.value = true;
  error.value = '';
  try {
    const ids = [...new Set(eligible.value.map(r => r.sceneId!))];
    saved.value = await addToSceneLibrary(ids.map(scene_id => ({ scene_id, tags: tags.value })));
    const scenes = useScenesStore();
    scenes.libraryMode = true;
    scenes.invalidateSearch();
  } catch (e) { error.value = e instanceof Error ? e.message : String(e); }
  finally { saving.value = false; }
}
</script>

<template>
  <Teleport to="body">
    <dialog ref="dialog" class="library-sandbox" aria-labelledby="library-title"
            @keydown.esc.stop.prevent="close" @cancel.prevent="close" @click="($event.target === dialog) && close()">
      <div class="sandbox-shell">
        <header><div><span class="eyebrow">SCENE LIBRARY · 批量入库</span><h2 id="library-title">为这批影像选择标签</h2><p>标签应用于本批可添加影像，保存到平台后可跨设备查看。</p></div>
          <button class="close" aria-label="关闭" :disabled="saving" @click="close">×</button></header>
        <template v-if="saved === null">
          <div class="sandbox-body">
            <aside><strong>本批 {{ records.length }} 张 · 可添加 {{ eligible.length }} 张</strong>
              <p v-if="skipped" class="warning">{{ skipped }} 张未关联盘阵或尚未解析，将跳过。请先关联盘阵路径再添加。</p>
              <p v-if="eligible.length > 200" class="warning">每批最多添加 200 张，请缩小选中范围。</p>
              <ul><li v-for="rec in records" :key="rec.id" :class="{ muted: !eligible.includes(rec) }">
                <AppIcon name="image" :size="20" /><div><span>{{ rec.name }}</span><small>{{ rec.W }} × {{ rec.H }} · {{ rec.stageLabel || '影像' }}</small></div>
              </li></ul>
            </aside>
            <main><section v-for="group in tagGroups" :key="group.key">
              <h3>{{ group.label }} <small>可多选</small></h3>
              <div class="tag-options"><button v-for="value in group.options" :key="value" type="button"
                :aria-pressed="tags[group.key].includes(value)" :disabled="saving"
                :class="{ chosen: tags[group.key].includes(value) }" @click="toggle(group.key, value)">{{ value }}</button></div>
              <template v-if="group.key === 'custom'">
                <div class="custom-entry"><input v-model="custom" maxlength="40" aria-label="自定义标签" placeholder="输入项目、批次或专题标签" :disabled="saving" @keydown.enter.prevent="addCustom" /><button :disabled="saving" @click="addCustom">添加标签</button></div>
                <div class="tag-options"><button v-for="value in tags.custom" :key="value" class="chosen" :disabled="saving" :aria-label="`移除标签 ${value}`" @click="toggle('custom', value)">{{ value }} ×</button></div>
              </template>
            </section><p class="hint">可不选标签直接添加。重复添加会合并已有标签，不复制源文件。</p></main>
          </div>
          <p v-if="error" role="alert" class="error">{{ error }}</p>
          <footer><span>保存文件引用与标签，源图保持原位</span><button :disabled="saving" @click="close">取消</button><button class="primary" :disabled="saving || !eligible.length || eligible.length > 200" @click="save">{{ saving ? '正在保存…' : `添加至场景库（${eligible.length}）` }}</button></footer>
        </template>
        <div v-else class="success" role="status"><h3>已添加 {{ saved }} 张影像</h3><p>标签已保存到平台服务端，可在场景库的“已入库”中查看。</p><button class="primary" @click="close">完成</button></div>
      </div>
    </dialog>
  </Teleport>
</template>

<style scoped>
.library-sandbox { width: min(1060px, 94vw); max-height: 88vh; padding: 0; border: 1px solid var(--line); border-radius: 20px; color: var(--ink); background: var(--surface); box-shadow: 0 28px 90px #102c3655; }
.library-sandbox::backdrop { background: rgba(12,30,35,.62); backdrop-filter: blur(4px); }
.sandbox-shell { display: flex; flex-direction: column; max-height: 88vh; }
header { display: flex; justify-content: space-between; padding: 26px 30px 20px; border-bottom: 1px solid var(--line); } h2 { margin: 8px 0; font-size: 24px; } p { font-size: 13px; line-height: 1.6; color: var(--ink-sub); } .eyebrow { font-size: 11px; letter-spacing: .12em; color: var(--accent-3); }
button, input { font: inherit; } button { cursor: pointer; border: 1px solid var(--line); border-radius: 8px; padding: 8px 14px; color: var(--ink); background: var(--surface); } button:disabled { opacity: .5; cursor: default; } button:focus-visible, input:focus-visible { outline: 2px solid var(--accent-3); outline-offset: 2px; } .close { align-self: flex-start; font-size: 24px; padding: 0 10px; }
.sandbox-body { display: grid; grid-template-columns: 300px 1fr; overflow: auto; min-height: 360px; } aside { padding: 24px; background: var(--surface-2); border-right: 1px solid var(--line); } aside strong { font-size: 13px; } ul { list-style: none; padding: 0; } li { display: flex; align-items: center; gap: 10px; padding: 12px 0; border-bottom: 1px solid var(--line); font-size: 12px; } li div { min-width: 0; } li span { display: block; overflow-wrap: anywhere; } li small { display: block; margin-top: 5px; color: var(--ink-sub); } .muted { opacity: .5; } .warning { color: #986530; }
main { padding: 12px 30px; } section { padding: 8px 0 14px; } h3 { font-size: 14px; margin-bottom: 12px; } h3 small { margin-left: 8px; font-size: 11px; font-weight: normal; color: var(--ink-sub); } .tag-options { display: flex; flex-wrap: wrap; gap: 8px; } .tag-options button { font-size: 12px; border-radius: 999px; } .tag-options .chosen { background: #e1f1eb; border-color: #85b9a9; color: #166c67; } .custom-entry { display: flex; gap: 8px; margin-bottom: 12px; } input { width: 100%; min-width: 0; border: 1px solid var(--line); border-radius: 8px; padding: 9px 12px; } .custom-entry button { flex: none; font-size: 12px; }
footer { display: flex; align-items: center; gap: 12px; padding: 18px 30px; border-top: 1px solid var(--line); } footer span { flex: 1; font-size: 12px; color: var(--ink-sub); } .primary { background: #166c67; border-color: #166c67; color: white; } .error { margin: 0; padding: 10px 30px; color: #b44343; } .success { text-align: center; padding: 65px 24px; }
@media(max-width: 700px) { .sandbox-body { grid-template-columns: 1fr; } aside { max-height: 180px; overflow: auto; } footer { flex-wrap: wrap; } footer span { flex-basis: 100%; } }
</style>
