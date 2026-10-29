<script setup lang="ts">
import {annotationVisual} from '../lib/annotationVisual';
import {computed} from 'vue';
import {useAnnotationsStore} from '../stores/annotations';
const props=defineProps<{data:unknown}>();const store=useAnnotationsStore();
const ids=computed(()=>{const d=props.data as {data?:{spatial_annotations?:{annotation_id:string}[]};spatial_annotations?:{annotation_id:string}[]} | undefined;return new Set((d?.data?.spatial_annotations??d?.spatial_annotations??[]).map(a=>a.annotation_id))});
const items=computed(()=>store.all.filter(a=>ids.value.has(a.annotation_id)));
</script>
<template><div v-if="items.length" class="spatial-references"><small>关联空间批注 · {{items.length}} 处 · 待核实</small><button v-for="a in items" :key="a.annotation_id" :data-annotation-id="a.annotation_id" :title="a.summary" :style="annotationVisual(a)" :class="{on:store.selected===a.annotation_id, hovered:store.hovered===a.annotation_id}" @pointerenter="store.hovered=a.annotation_id" @pointerleave="store.hovered=null" @focus="store.hovered=a.annotation_id" @blur="store.hovered=null" :aria-pressed="store.selected===a.annotation_id" @click="store.focus(a)">{{String(store.all.indexOf(a)+1).padStart(2,'0')}} {{a.title}}</button></div></template>
<style scoped>.spatial-references{font-size:12px;margin:8px 0}small{display:block;color:#687a74}button{cursor:pointer;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;background:var(--annotation-soft,#f5faf8);color:var(--annotation-selected,#155c66);border:1px solid #d8e4df;border-color:color-mix(in srgb,var(--annotation-color,#287f8b) 28%,white);border-radius:8px;padding:5px 8px;margin:4px 4px 0 0}button.on{outline:1px solid var(--annotation-color,#287f8b);background:var(--annotation-soft)}button.hovered{border-color:var(--annotation-color,#287f8b)}button{transition:background-color 160ms,border-color 160ms;min-height:28px}button:focus-visible{outline:2px solid var(--annotation-color,#287f8b)}</style>
