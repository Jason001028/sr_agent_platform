<script setup lang="ts">
import { computed } from 'vue';
import type { BatchSnapshot } from '../lib/api';
import { focusMissionItems } from '../lib/batchMission';
import BatchMissionItem from './BatchMissionItem.vue';
const props = defineProps<{ snapshot: BatchSnapshot }>();
const rows = computed(() => focusMissionItems(props.snapshot));
</script>
<template><div v-if="rows.length" class="mission-focus-list" data-e2e="batch-jobs"><BatchMissionItem v-for="row in rows" :key="row.item_id" :item="row" /></div></template>
<style scoped>.mission-focus-list{display:grid;gap:7px}</style>
