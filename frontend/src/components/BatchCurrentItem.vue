<script setup lang="ts">
import type { BatchSnapshot } from '../lib/api';
import BatchMissionItem from './BatchMissionItem.vue';
defineProps<{ snapshot: BatchSnapshot }>();
</script>
<template><div v-if="snapshot.producer.current" class="batch-current" data-e2e="batch-current"><BatchMissionItem :item="snapshot.producer.current" /></div></template>
