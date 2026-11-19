<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { installButtonPress } from '../lib/buttonPress';

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

/* 行内「跳转至对应景」：按下回弹走查看器统一的那套（0.94 / 弹簧 260·12）。
   只在真浏览器里挂：jsdom 没有 matchMedia，单测不该被动画初始化拖住。 */
const cardRoot = ref<HTMLElement | null>(null);
let disposePress: (() => void) | undefined;
onMounted(() => {
  if (cardRoot.value && typeof window.matchMedia === 'function') {
    disposePress = installButtonPress(cardRoot.value, '.jump-scene');
  }
});
onBeforeUnmount(() => disposePress?.());

/** 正在跳的行（同一时刻只放一个，避免两行并发去开景/跑差异）。 */
const jumping = ref<string | null>(null);
async function onJump(row: InspectionRow) {
  if (jumping.value) return;
  jumping.value = row.item_id;
  try {
    // 查看器 / 差异定位整条链只在真正点下去时载入：这张表在聊天页也会出现，
    // 只是渲染结果不该带上它。
    const { jumpToInspectionScene } = await import('../lib/inspectionJump');
    await jumpToInspectionScene(row.name, row.images?.pan ?? row.images?.nosr);
  } finally {
    jumping.value = null;
  }
}
const imageColumns = [
  { role: 'pan', label: 'PAN' },
  { role: 'nosr', label: 'NOSR' },
  { role: 'difference', label: '差分结果' },
] as const;
</script>

<template>
  <section ref="cardRoot" class="inspection-card">
    <header><strong>质检结果 · {{ rows.length }} 景</strong><span>整批检测结束</span></header>
    <p>仅质检 · {{ {absdiff:'绝对差异',ssim:'结构差异 SSIM',smart:'LTD 纹理退化'}[String(data?.detection_method) as 'absdiff'|'ssim'|'smart'] }}</p>
    <p v-if="failed">{{ failed }} 景检测失败，详见表内原因。</p>
    <button v-if="rows.length" class="expand-table" type="button" @click="openFullTable">展开完整表格 · {{ rows.length }} 景</button>
    <dialog ref="dialog" aria-label="完整质检表格" @close="expanded = false">
      <header><strong>质检结果 · {{ rows.length }} 景</strong><button class="dialog-close" type="button" autofocus @click="dialog?.close()">关闭</button></header>
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
            <button
              type="button"
              class="jump-scene"
              data-e2e="inspection-jump-scene"
              :disabled="!!jumping"
              :title="jumping === row.item_id
                ? '正在打开这一景并加载原图/NOSR 差异定位…'
                : '跳到这一景：加载原图与 NOSR 的 SSIM 差异定位，并打开点选对比'"
              @click="onJump(row)"
            >{{ jumping === row.item_id ? '跳转中…' : '跳转至对应景' }}</button>
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
.expand-table{margin-top:10px;padding:7px 14px;border:1px solid transparent;border-radius:8px;background:var(--tb-primary);color:var(--tb-primary-fg);font-size:12px;font-weight:600;transition:background var(--motion-hover) var(--ease-standard)}
.expand-table:hover{background:var(--tb-primary-hover)}
.expand-table:focus-visible{outline:2px solid var(--tb-focus);outline-offset:2px}
/* 与工具栏「绘制掩码」同款（.outbtn 默认态）：白底、1px 描边、8px 圆角。 */
.dialog-close{display:inline-flex;align-items:center;justify-content:center;flex:none;height:32px;padding:0 10px;border:1px solid var(--tb-border);border-radius:8px;background:var(--tb-surface);color:var(--tb-fg);font-family:inherit;font-size:13px;font-weight:500;line-height:1;white-space:nowrap;cursor:pointer;transition:background var(--motion-hover) var(--ease-standard),border-color var(--motion-hover) var(--ease-standard)}
.dialog-close:hover{background:var(--tb-hover)}
.dialog-close:focus-visible{outline:2px solid var(--tb-focus);outline-offset:1px}
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
/* 行内入口：白底描边，按压回弹由 lib/buttonPress 统一驱动（这里只给常态阴影，
   按下时动画自己把阴影收到 none，松手用同一条弹簧弹回）。 */
.jump-scene{display:flex;width:max-content;align-items:center;justify-content:center;height:23px;margin-top:5px;padding:0 9px;border:1px solid var(--tb-border);border-radius:7px;background:var(--tb-surface);color:var(--tb-fg);font:inherit;font-size:10.5px;font-weight:500;line-height:1;white-space:nowrap;cursor:pointer;box-shadow:0 1px 2px rgb(23 62 49 / 14%),0 2px 5px rgb(23 62 49 / 8%);transition:background var(--motion-hover) var(--ease-standard),border-color var(--motion-hover) var(--ease-standard)}
.jump-scene:hover:not(:disabled){background:var(--tb-hover);border-color:var(--tb-focus)}
.jump-scene:focus-visible{outline:2px solid var(--tb-focus);outline-offset:1px}
.jump-scene:disabled{opacity:.55;cursor:default}
td img{display:block;width:100%;height:72px;object-fit:contain;background:#eef2ef;border-radius:4px}
</style>
