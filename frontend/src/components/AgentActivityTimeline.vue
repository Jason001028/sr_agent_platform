<script setup lang="ts">
import { computed } from 'vue';
import type { ActivityGroup } from '../lib/agentActivity';
import { activityDisclosure, setActivityDisclosure } from '../lib/activityDisclosure';
import AgentActivityStep from './AgentActivityStep.vue';
const props = defineProps<{ group: ActivityGroup; sessionKey?: string | null; developerMode?: boolean }>();
const identity = computed(() => `${props.sessionKey ?? 'unbound'}:${props.group.key}`);
const running = computed(() => props.group.steps.filter(s => s.state === 'running'));
const completed = computed(() => props.group.steps.filter(s => s.state === 'success').length);
const pinned = computed(() => props.group.steps.filter(s => s.state !== 'success'));
const single = computed(() => props.group.steps.length === 1);
const expanded = computed(() => activityDisclosure.get(identity.value) ?? running.value.length > 0);
const heading = computed(() => completed.value ? `已完成 ${completed.value} 项操作`
  : running.value.length ? `正在执行 ${running.value.length} 项操作` : `${props.group.steps.length} 项操作待核对`);
function toggle() { setActivityDisclosure(identity.value,!expanded.value); }
</script>
<template>
  <section class="activity-timeline agent-trace" aria-label="工具执行轨迹" :data-group="group.key">
    <button v-if="!single" type="button" class="group-toggle" :aria-expanded="expanded" @click="toggle">
      <span class="trace-node" :class="{ active: running.length > 0, issue: pinned.some(s => s.state === 'failed' || s.state === 'interrupted') }" aria-hidden="true"></span><span>{{ heading }}</span>
      <span v-if="completed && running.length" class="remaining"> · {{ running.length }} 项执行中</span>
    </button>
    <ol v-if="single || !expanded && pinned.length" class="steps pinned">
      <AgentActivityStep v-for="step in (single ? group.steps : pinned)" :key="step.key" :step="step" :developer-mode="developerMode" />
    </ol>
    <Transition name="activity-fold"><div v-if="!single && expanded" class="fold">
      <ol class="steps"><AgentActivityStep v-for="step in group.steps" :key="step.key" :step="step" :developer-mode="developerMode" /></ol>
    </div></Transition>
  </section>
</template>
<style scoped>
.activity-timeline { min-width:0; max-width:100%; margin:4px 0; font-size:12px; color:var(--ink-sub); }
.group-toggle { display:flex; align-items:center; gap:8px; min-height:28px; padding:0; border:0; background:none; color:var(--ink-sub); font:inherit; cursor:pointer; max-width:100%; text-align:left; }
.chevron { width:14px; flex:0 0 14px; font-size:18px; text-align:center; color:var(--ink-faint); transition:transform 180ms ease; } .chevron.expanded { transform:rotate(90deg); }
.remaining { font-size:inherit; color:var(--ink-faint); } .steps { position:relative; padding:0; margin:0; min-height:0; list-style:none; }
.fold { display:grid; grid-template-rows:1fr; } .fold > ol { overflow:hidden; }
.activity-fold-enter-active,.activity-fold-leave-active { transition:grid-template-rows 180ms ease,opacity 180ms ease; }
.activity-fold-enter-from,.activity-fold-leave-to { grid-template-rows:0fr; opacity:0; }
.group-toggle:focus-visible { outline:2px solid var(--tb-focus); outline-offset:3px; border-radius:3px; }
@media(prefers-reduced-motion:reduce) { * { transition:none!important; } }
</style>
