<script setup lang="ts">
import { ref } from 'vue';
import type { RepairLandItem, RepairPlan } from '../lib/api';
import router from '../router';
import { useScenesStore } from '../stores/scenes';
import { useViewerStore } from '../stores/viewer';

defineProps<{ plan: RepairPlan }>();
const viewer = useViewerStore();
const scenes = useScenesStore();
const busy = ref(false);
const error = ref('');

async function review(item: RepairLandItem) {
  if (busy.value || !item.scene_id || !item.preview_url) return;
  busy.value = true;
  error.value = '';
  try {
    if (viewer.drawMode) throw new Error('请先退出绘制或编辑。');
    viewer.noteUserIntent();
    const sequence = viewer.userIntentSeq;
    const stillWanted = () => viewer.userIntentSeq === sequence && !viewer.drawMode;
    const loaded = viewer.recsOrdered.find(rec => rec.sceneId === item.scene_id && rec.srcRev === item.preview_revision
      && rec.route === 'jpg');
    if (loaded) {
      await viewer.activate(loaded.id, undefined, 'follow');
    } else if (item.image_path) {
      const problem = await scenes.openQuiet(item.image_path, 'follow', { stillWanted });
      if (problem) throw new Error(problem);
    } else {
      throw new Error('请先在 Viewer 打开计划对应的 PAN。');
    }
    if (!stillWanted()) return;
    viewer.repairLandTarget = item;
    await router.push('/viewer');
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <details v-if="plan.land_constraint" class="land-summary">
    <summary data-e2e="repair-land-counts">
      候选 {{ plan.land_constraint.candidates }} · 完全排除 {{ plan.land_constraint.excluded }} ·
      部分裁剪 {{ plan.land_constraint.clipped }} · 可执行 {{ plan.land_constraint.executable }}
    </summary>
    <ul>
      <li v-for="item in plan.land_constraint.items" :key="item.item_id">
        <span>{{ plan.targets.items.find(row => row.item_id === item.item_id)?.name || item.item_id }}</span>
        <span>候选 {{ item.candidates }} · 排除 {{ item.excluded }} · 裁剪 {{ item.clipped }} · 可执行 {{ item.executable }}</span>
        <button v-if="item.preview_url && item.scene_id" type="button" :disabled="busy" @click="review(item)">在 Viewer 核对</button>
      </li>
    </ul>
    <p v-if="error" role="status">{{ error }}</p>
  </details>
</template>

<style scoped>
.land-summary { font-size: .85em; line-height: 1.5; }
.land-summary summary { cursor: pointer; }
.land-summary ul { list-style: none; padding: 0; margin: .4em 0; max-height: 14rem; overflow: auto; }
.land-summary li { display: flex; flex-direction: column; align-items: flex-start; gap: .15em; margin: .4em 0; overflow-wrap: anywhere; }
</style>
