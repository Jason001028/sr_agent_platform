<script setup lang="ts">
import { computed, ref } from 'vue';

interface InspectionRow {
  item_id: string;
  name: string;
  status: string;
  candidate_count?: number;
  reason?: string;
  images?: Partial<Record<'pan' | 'nosr' | 'difference', string>>;
}

const props = defineProps<{ data?: Record<string, unknown> }>();
const rows = computed(() => (props.data?.items ?? []) as InspectionRow[]);
const failed = computed(() => rows.value.filter(row => row.status === 'failed').length);
const dialog = ref<HTMLDialogElement | null>(null);
const fullTable = ref<HTMLElement | null>(null);
const expanded = ref(false);
function openFullTable() {
  dialog.value?.showModal();
  expanded.value = true;
}
const imageColumns = [
  { role: 'pan', label: 'PAN' },
  { role: 'nosr', label: 'NOSR' },
  { role: 'difference', label: '差异图' },
] as const;
</script>

<template>
  <section class="inspection-card">
    <header><strong>质检结果 · {{ rows.length }} 景</strong><span>整批检测结束</span></header>
    <p>仅质检 · {{ {absdiff:'绝对差异',ssim:'结构差异 SSIM',smart:'LTD 纹理退化'}[String(data?.detection_method) as 'absdiff'|'ssim'|'smart'] }}</p>
    <p v-if="failed">{{ failed }} 景检测失败，详见表内原因。</p>
    <button v-if="rows.length" type="button" @click="openFullTable">展开完整表格 · {{ rows.length }} 景</button>
    <dialog ref="dialog" aria-label="完整质检表格" @close="expanded = false">
      <header><strong>质检结果 · {{ rows.length }} 景</strong><button type="button" autofocus @click="dialog?.close()">关闭</button></header>
      <div ref="fullTable"></div>
    </dialog>
    <Teleport :to="fullTable ?? 'body'" :disabled="!expanded">
    <div class="table-scroll" tabindex="0" role="region" aria-label="质检结果表格">
    <table>
      <thead><tr><th scope="col">景名 / 结果</th><th v-for="column in imageColumns" :key="column.role" scope="col">{{ column.label }}</th></tr></thead>
      <tbody>
        <tr v-for="row in rows" :key="row.item_id">
          <td>
            <span class="scene-name" :title="row.name">{{ row.name }}</span>
            <span>{{ row.status === 'failed' ? (row.reason || '检测失败') : row.candidate_count + ' 处候选' }}</span>
          </td>
          <td v-for="column in imageColumns" :key="column.role">
            <img v-if="row.images?.[column.role]" :src="row.images[column.role]" :alt="column.label" loading="lazy" />
            <span v-else>—</span>
          </td>
        </tr>
      </tbody>
    </table>
    </div>
    </Teleport>
    <small>候选需人工判断，仅检测，不提交修复。</small>
  </section>
</template>

<style scoped>
.inspection-card{min-width:0;padding:12px;border:1px solid var(--tb-border);border-radius:12px;background:var(--tb-bg);color:var(--tb-fg);font-size:12px}
header{display:flex;justify-content:space-between;gap:8px}
header span,p,small{color:var(--tb-muted);font-size:11px}
small{display:block;margin-top:8px}
button{font:inherit;cursor:pointer}
.table-scroll{max-height:min(360px,45vh);overflow:auto;overscroll-behavior:contain;margin-top:8px}
table{width:100%;min-width:260px;table-layout:fixed;border-collapse:separate;border-spacing:0;font-size:10px}
thead th{position:sticky;top:0;background:var(--tb-bg,white)}
dialog{box-sizing:border-box;width:min(960px,calc(100vw - 24px));max-height:90vh;padding:16px;border:1px solid var(--tb-border,#ddd);border-radius:12px;background:var(--tb-bg,white);color:var(--tb-fg,#222)}
dialog::backdrop{background:rgb(0 0 0 / 30%)}
dialog .table-scroll{max-height:75vh}
dialog table{font-size:13px}
dialog td img{height:140px}
th,td{padding:5px 3px;text-align:left;vertical-align:top;border-bottom:1px solid var(--tb-border);overflow-wrap:anywhere}
th:first-child{width:31%}
.scene-name{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:500}
td img{display:block;width:100%;height:72px;object-fit:contain;background:#eef2ef;border-radius:4px}
</style>
