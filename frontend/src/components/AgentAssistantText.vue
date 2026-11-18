<script setup lang="ts">
import { computed } from 'vue';
import type { ChatMsg } from '../stores/chat';
import MarkdownBody from './MarkdownBody.vue';

const props = defineProps<{ message: ChatMsg; developerMode?: boolean }>();
const inspectionCommentary = computed(() =>
  props.message.toolCalls?.some(call => call.name === 'inspect_repair_workspace'));
</script>

<template>
  <details v-if="inspectionCommentary && developerMode" class="inspection-commentary">
    <summary>开发者详情 · 质检过程说明</summary>
    <MarkdownBody :source="message.content" />
  </details>
  <div v-else-if="!inspectionCommentary" class="bubble assistant">
    <MarkdownBody :source="message.content" />
  </div>
</template>

<style scoped>
.inspection-commentary { font-size: 11px; color: var(--ink-faint); overflow-wrap: anywhere; }
summary { cursor: pointer; }
</style>
