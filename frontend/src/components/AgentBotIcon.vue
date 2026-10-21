<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
export type AgentStatus = 'idle' | 'thinking' | 'responding';
const props = withDefaults(defineProps<{ status: AgentStatus; size?: number; label?: string }>(), { size: 16, label: '' });
const idleAction = ref<'normal' | 'left' | 'right' | 'blink'>('normal');
let timer: ReturnType<typeof setTimeout> | null = null; let actionTimer: ReturnType<typeof setTimeout> | null = null;
function clearTimers() { if (timer) clearTimeout(timer); if (actionTimer) clearTimeout(actionTimer); timer = actionTimer = null; }
function scheduleIdle() { clearTimers(); if (props.status !== 'idle' || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return; timer = setTimeout(() => { const action = Math.random() < .55 ? 'blink' : (Math.random() < .5 ? 'left' : 'right'); idleAction.value = action; actionTimer = setTimeout(() => { idleAction.value = 'normal'; scheduleIdle(); }, action === 'blink' ? 170 : 650); }, 4000 + Math.random() * 5000); }
watch(() => props.status, () => { idleAction.value = 'normal'; scheduleIdle(); }, { immediate: true }); onBeforeUnmount(clearTimers);
const iconClass = computed(() => ['agent-bot-icon', `is-${props.status}`, `idle-${idleAction.value}`]);
</script>
<template>
  <svg v-if="props.status === 'idle'" :class="iconClass" xmlns="http://www.w3.org/2000/svg" :width="props.size" :height="props.size" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" role="img" :aria-label="props.label || undefined" :aria-hidden="props.label ? undefined : 'true'">
    <path d="M12 8V4H8" /><rect width="16" height="12" x="4" y="8" rx="2" /><path d="M2 14h2" /><path d="M20 14h2" /><g class="bot-gaze"><g class="bot-eyes"><path d="M9 13v2" /><path d="M15 13v2" /></g></g>
  </svg>
  <svg v-else class="agent-bot-icon agent-bot-chat" xmlns="http://www.w3.org/2000/svg" :width="props.size" :height="props.size * .875" viewBox="0 0 32 28" fill="none" role="img" :aria-label="props.label || 'Agent 正在对话'" :aria-hidden="props.label ? undefined : 'true'">
    <g class="chat-body" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8V4H8" /><rect width="16" height="12" x="4" y="8" rx="2.5" fill="currentColor" fill-opacity=".08" /><path d="M2 14h2M20 14h2" /><g class="chat-gaze"><g class="chat-blink"><path d="M9 13v2M15 13v2" /></g></g><path d="M9 18h6" opacity=".7" /></g>
    <g class="chat-speech" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M21.2 2.5h6.1a3.2 3.2 0 0 1 3.2 3.2v2.45a3.2 3.2 0 0 1-3.2 3.2h-2.35l-2.05 2.05.3-2.05h-2a3.2 3.2 0 0 1-3.2-3.2V5.7a3.2 3.2 0 0 1 3.2-3.2Z" fill="#fff" stroke-width="1.25" /><g fill="currentColor" stroke="none"><circle class="chat-dot" cx="21.8" cy="6.9" r=".85" /><circle class="chat-dot" cx="24.7" cy="6.9" r=".85" /><circle class="chat-dot" cx="27.6" cy="6.9" r=".85" /></g></g>
  </svg>
</template>
<style scoped>
.agent-bot-icon { display: block; flex: none; overflow: visible; }
.bot-gaze, .bot-eyes, .chat-gaze, .chat-blink, .chat-body, .chat-speech, .chat-dot { transform-box: fill-box; transform-origin: center; }
.bot-gaze { transition: transform 420ms cubic-bezier(.22,.8,.35,1); }.idle-left .bot-gaze { transform: translateX(-.8px); }.idle-right .bot-gaze { transform: translateX(.8px); }.idle-blink .bot-eyes { animation: bot-blink 150ms ease-in-out 1; }
.chat-body { animation: chat-bounce 1.35s ease-in-out infinite; transform-origin: center bottom; }.chat-speech { animation: chat-speech 1.15s cubic-bezier(.34,1.2,.64,1) infinite; transform-origin: center bottom; }.chat-gaze { animation: chat-look 6.8s ease-in-out infinite; }.chat-blink { animation: chat-blink 5.4s linear infinite; }.chat-dot { animation: dot-wave 1.15s ease-in-out infinite; }.chat-dot:nth-child(2) { animation-delay: .13s; }.chat-dot:nth-child(3) { animation-delay: .26s; }
@keyframes chat-bounce { 0%,100% { transform: translateY(0) scaleY(1); } 18% { transform: translateY(-.45px) scaleY(1.008); } 36% { transform: translateY(0) scaleY(.996); } 54% { transform: translateY(-.28px) scaleY(1.004); } 72% { transform: translateY(0) scaleY(1); } }
@keyframes chat-speech { 0%,100% { transform: translateY(0) rotate(0); } 18% { transform: translateY(-1.1px) rotate(-2deg); } 35% { transform: translateY(-.2px) rotate(1deg); } 52% { transform: translateY(-.7px) rotate(-1deg); } }
@keyframes chat-look { 0%,24%,100% { transform: translateX(0); } 29%,33% { transform: translateX(-1px); } 61%,65% { transform: translateX(1px); } }
@keyframes chat-blink { 0%,17%,19%,69%,71%,100% { transform: scaleY(1); } 18%,70% { transform: scaleY(.08); } }
@keyframes dot-wave { 0%,100% { transform: translateY(0); opacity: .45; } 18% { transform: translateY(-1.25px); opacity: 1; } 36% { transform: translateY(0); opacity: .72; } }
@keyframes bot-blink { 0%,100% { transform: scaleY(1); } 45%,65% { transform: scaleY(.08); } }
@media (prefers-reduced-motion: reduce) { .idle-blink .bot-eyes, .chat-body, .chat-speech, .chat-gaze, .chat-blink, .chat-dot { animation: none; } .bot-gaze { transition: none; } }
</style>
