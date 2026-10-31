<script setup lang="ts">
import { computed } from 'vue';
import { analysisPromptSummary } from '../lib/analysisPrompt';
const props = defineProps<{ text: string }>();
const summary = computed(() => analysisPromptSummary(props.text));
</script>

<template>
  <div class="user-message-text">
    <p>{{ summary ?? text }}</p>
    <details v-if="summary" class="full-instruction">
      <summary>查看完整指令</summary>
      <p>{{ text }}</p>
    </details>
  </div>
</template>

<style scoped>
p { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.full-instruction { margin-top: 6px; font-size: 11px; line-height: 1.6; white-space: normal; }
.full-instruction summary { cursor: pointer; opacity: .72; width: fit-content; }
.full-instruction summary:hover { opacity: 1; }
.full-instruction > p { margin-top: 6px; opacity: .85; }
</style>
