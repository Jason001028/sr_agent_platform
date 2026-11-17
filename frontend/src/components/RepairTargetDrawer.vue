<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
defineProps<{ rows: { item_id: string; ordinal: number; name: string; viewerLabel: number; status?: string }[]; total: number; loading?: boolean; error?: string }>();
const emit = defineEmits<{ close: [] }>();
const panel = ref<HTMLElement>();
const returnFocus = document.activeElement as HTMLElement | null;
const background: { element: Element; inert: boolean }[] = [];
let previousOverflow = '';
function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') { e.stopPropagation(); emit('close'); }
  if (e.key === 'Tab') {
    const nodes = panel.value?.querySelectorAll<HTMLElement>('button:not(:disabled), [tabindex="0"]');
    if (!nodes?.length) return;
    const first = nodes[0], last = nodes[nodes.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === panel.value)) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
}
onMounted(() => {
  previousOverflow = document.body.style.overflow;
  document.body.style.overflow = 'hidden';
  for (const element of document.body.children) {
    if (element.contains(panel.value!)) continue;
    background.push({element, inert: element.hasAttribute('inert')});
    element.setAttribute('inert', '');
  }
  void nextTick(() => panel.value?.focus());
});
onBeforeUnmount(() => {
  for (const row of background) if (!row.inert) row.element.removeAttribute('inert');
  document.body.style.overflow = previousOverflow;
  returnFocus?.focus();
});
</script>
<template>
  <div class="repair-drawer-backdrop" @click.self="emit('close')" @keydown="onKey">
    <section ref="panel" class="repair-drawer" role="dialog" aria-modal="true" aria-label="修复目标清单" tabindex="-1">
      <header><div><small>修复目标</small><h2>全部 {{ total }} 景</h2></div><button aria-label="关闭目标清单" @click="emit('close')">×</button></header>
      <p class="legend">数字标签对应 Viewer · 按生产清单顺序排列</p>
      <p v-if="loading" role="status">正在读取完整清单…</p><p v-if="error" role="alert">{{ error }}</p>
      <ol class="target-list" tabindex="0" aria-label="目标影像列表">
        <li v-for="row in rows" :key="row.item_id">
          <span class="tag" :title="row.viewerLabel ? 'Viewer 数字标签' : '当前 Viewer 未打开此景'">{{ row.viewerLabel || '—' }}</span>
          <span class="filename" :title="row.name">{{ row.name }}</span><small :title="`生产序列 ${row.ordinal}`">#{{ row.ordinal }}</small>
        </li>
      </ol>
      <footer>已显示 {{ rows.length }} / {{ total }} 景<span v-if="rows.some(r => !r.viewerLabel)"> · — 表示当前 Viewer 无对应标签</span></footer>
    </section>
  </div>
</template>
<style scoped>
.repair-drawer-backdrop { position:fixed; inset:0; z-index:1200; display:flex; justify-content:flex-end; background:rgb(23 63 59 / 24%); }
.repair-drawer { width:min(440px, 100vw); height:100%; min-width:0; box-sizing:border-box; padding:24px 20px; background:var(--tb-bg); color:var(--tb-fg); box-shadow:-12px 0 40px rgb(23 63 59 / 10%); display:flex; flex-direction:column; gap:14px; outline:none; }
header { display:flex; justify-content:space-between; align-items:center; } header small { color:var(--tb-muted); font-size:11px; } h2 { font-size:20px; margin:5px 0 0; font-weight:600; }
button { width:34px; height:34px; border:1px solid var(--tb-border); border-radius:9px; background:var(--surface); color:var(--tb-fg); cursor:pointer; font-size:22px; }
p { margin:0; font-size:12px; overflow-wrap:anywhere; } .legend, footer { color:var(--tb-muted); font-size:11px; }
.target-list { flex:1; min-height:0; overflow:auto; overscroll-behavior:contain; margin:0; padding:0; list-style:none; }
li { display:grid; grid-template-columns:30px minmax(0,1fr) 36px; gap:10px; align-items:center; padding:13px 0; border-bottom:1px solid var(--tb-divider); font-size:12px; }
.tag { background:var(--tb-active-bg); color:var(--tb-active-fg); border-radius:6px; padding:4px 0; text-align:center; font-variant-numeric:tabular-nums; }
.filename { overflow:hidden; white-space:nowrap; text-overflow:ellipsis; } li small { text-align:right; color:var(--tb-muted); }
footer { line-height:1.7; }
</style>
