<script setup lang="ts">
import { computed } from 'vue';
import { analysisPromptSummary } from '../lib/analysisPrompt';
const props = defineProps<{ text: string }>();
const summary = computed(() => analysisPromptSummary(props.text));
</script>

<template>
  <div class="user-message-text">
    <p>{{ summary ?? text }}</p>
  </div>
</template>

<style scoped>
p { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
