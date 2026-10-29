<script setup lang="ts">
import {annotationVisual} from '../lib/annotationVisual';
import {computed} from 'vue';
import {useAnnotationsStore} from '../stores/annotations';
import {useViewerStore} from '../stores/viewer';
import {imageRef} from '../lib/spatial';
import {isDeveloperMode} from '../lib/developerMode';
const store=useAnnotationsStore(), viewer=useViewerStore();
const items=computed(()=>store.all.filter(a=>viewer.activeRec?.thumb&&imageRef(viewer.activeRec)?.image_key===a.image_ref.image_key));
const unbound=computed(()=>store.all.filter(a=>!viewer.panes.some(p=>p.rec?.thumb&&imageRef(p.rec)?.image_key===a.image_ref.image_key)));
const dev=import.meta.env.DEV;
</script>
<template>
 <div class="annotation-manager">
 <details v-if="items.length"><summary>空间批注 {{items.length}}</summary>
 <label><input v-model="store.hidden" type="checkbox">隐藏批注</label>
 <button v-for="a in items" :key="a.annotation_id" :data-annotation-id="a.annotation_id" :style="annotationVisual(a)" :class="{on:store.selected===a.annotation_id, hovered:store.hovered===a.annotation_id}" @pointerenter="store.hovered=a.annotation_id" @pointerleave="store.hovered=null" @focus="store.hovered=a.annotation_id" @blur="store.hovered=null" :aria-pressed="store.selected===a.annotation_id" @click="store.focus(a)">{{String(store.all.indexOf(a)+1).padStart(2,'0')}} {{a.title}} · 待核实</button>
 </details>
 <button v-if="dev && isDeveloperMode() && viewer.activeRec?.thumb" @click="store.mock()">Mock 空间批注（仅开发演示）</button>
 <button v-if="store.demo.length" @click="store.demo=[]">清除 Mock 演示</button>
 <details v-if="unbound.length"><summary>未绑定批注 {{unbound.length}}</summary><button v-for="a in unbound" :key="a.annotation_id" @click="store.rebind(a)">{{a.title}} ? 关联当前影像</button></details>
 <p v-if="store.status" role="status">{{store.status}}</p>
 </div>
</template>
<style scoped>.annotation-manager{font-size:12px;padding:6px 12px;color:var(--text-muted,#687a74)}summary,button{cursor:pointer}button{display:block;max-width:100%;overflow:hidden;text-overflow:ellipsis;border:1px solid #d8e4df;border-radius:8px;background:white;color:var(--annotation-selected,#155c66);margin:5px 0;padding:5px 8px;text-align:left}.on{outline:1px solid var(--annotation-color,#287f8b);background:var(--annotation-soft)}.hovered{border-color:var(--annotation-color,#287f8b)}button{transition:background-color 160ms,border-color 160ms}p{margin:4px 0}</style>
