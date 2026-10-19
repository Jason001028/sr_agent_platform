<script setup lang="ts">
import { computed, onMounted } from 'vue';
import { usePluginStore } from '../stores/plugins.js';
import type { ManagedPlugin, PluginOperation } from '../lib/api.js';

const plugins = usePluginStore();

const machine = computed(() => plugins.machines.find(
  (m) => m.machine_id === plugins.selectedMachineId,
));
const currentMachine = computed(() => plugins.machines.find(
  (m) => m.machine_id === plugins.currentMachineId,
));

function stateText(state: string): string {
  return ({ enabled: '已启用', disabled: '已禁用',
    uninstalled: '未安装', unknown: '未知' } as Record<string, string>)[state] ?? state;
}

function stateClass(state: string): string {
  return `plug-${state}`;
}

function actionFor(p: ManagedPlugin): PluginOperation['action'] {
  if (p.actual_state === 'enabled') return 'disable';
  if (p.actual_state === 'disabled') return 'enable';
  return 'install';
}

function actionText(p: ManagedPlugin): string {
  if (p.actual_state === 'enabled') return '禁用';
  if (p.actual_state === 'disabled') return '启用';
  return '安装';
}

async function apply(p: ManagedPlugin): Promise<void> {
  await plugins.action(p.plugin_id, actionFor(p));
}

async function uninstall(p: ManagedPlugin): Promise<void> {
  await plugins.action(p.plugin_id, 'uninstall');
}

onMounted(() => void plugins.refresh());
</script>

<template>
  <div class="plugins-page">
    <div class="plugins-head">
      <div>
        <h2>插件管理</h2>
        <p class="plugins-sub">按机器维护插件目标状态；远程机器由 Agent 执行并回报结果。</p>
      </div>
      <div class="plugins-head-actions">
        <div class="current-machine-chip">
          <span>当前机器</span>
          <strong>{{ currentMachine?.name ?? plugins.currentMachineId }}</strong>
          <code>{{ plugins.currentMachineId }}</code>
        </div>
        <span class="developer-status" :class="{ on: plugins.developerMode }">
          {{ plugins.developerMode ? '开发者模式已开启' : '普通模式' }}
        </span>
        <button type="button" class="btn ghost" :disabled="plugins.loading"
                @click="plugins.refresh()">
          {{ plugins.loading ? '刷新中…' : '刷新' }}
        </button>
      </div>
    </div>

    <p v-if="plugins.error" class="plugins-error">{{ plugins.error }}</p>

    <div class="plugins-layout">
      <aside class="plugins-machines">
        <div class="plugins-section-title">机器</div>
        <div v-if="!plugins.developerMode" class="current-only">
          普通模式只显示当前机器。开启开发者模式后可查看和控制其他机器。
        </div>
        <template v-else>
          <button v-for="m in plugins.machines" :key="m.machine_id" type="button"
                  class="machine-row" :class="{ selected: m.machine_id === plugins.selectedMachineId }"
                  @click="plugins.selectMachine(m.machine_id)">
            <span class="machine-name">{{ m.name }}</span>
            <span class="machine-state" :class="m.status === 'online' ? 'online' : 'offline'">
              {{ m.status === 'online' ? '在线' : m.status }}
            </span>
            <code>{{ m.machine_id }}</code>
          </button>
        </template>
        <p v-if="!plugins.machines.length && !plugins.loading" class="plugins-empty">
          暂无已注册机器。
        </p>
      </aside>

      <section class="plugins-main">
        <div class="plugins-main-head">
          <span class="plugins-section-title">插件</span>
          <span v-if="machine" class="plugins-machine-label">{{ machine.name }}</span>
        </div>
        <div v-if="plugins.loading" class="plugins-empty">加载插件…</div>
        <div v-else-if="!plugins.plugins.length" class="plugins-empty">暂无插件。</div>
        <div v-else class="plugins-table-wrap">
          <table class="plugins-table">
            <thead>
              <tr><th>插件</th><th>版本</th><th>目标状态</th><th>实际状态</th><th>操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="p in plugins.plugins" :key="p.plugin_id">
                <td class="plugin-info">
                  <strong>{{ p.name }}</strong>
                  <code>{{ p.plugin_id }}</code>
                  <span>{{ p.description }}</span>
                </td>
                <td class="mono">{{ p.version }}</td>
                <td><span class="plugin-tag" :class="stateClass(p.desired_state)">
                  {{ stateText(p.desired_state) }}</span></td>
                <td><span class="plugin-tag" :class="stateClass(p.actual_state)">
                  {{ stateText(p.actual_state) }}</span>
                  <small v-if="p.last_error" class="plugin-error">{{ p.last_error }}</small>
                </td>
                <td class="plugin-actions">
                  <button type="button" class="btn mini" :disabled="plugins.pending !== null"
                          @click="apply(p)">
                    {{ plugins.pending === p.plugin_id ? '处理中…' : actionText(p) }}
                  </button>
                  <button v-if="p.actual_state !== 'uninstalled'" type="button"
                          class="btn mini ghost" :disabled="plugins.pending !== null"
                          @click="uninstall(p)">卸载</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
/* 与场景库、聊天、任务队列共用同一内容列：宽度始终跟随 --page-w。 */
.plugins-page { width: 100%; max-width: var(--page-w); margin: 0 auto; padding: 10px 20px 44px; }
.plugins-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
.plugins-head h2 { margin: 0; font-size: 22px; color: var(--ink); }
.plugins-sub { margin: 5px 0 0; color: var(--ink-sub); font-size: 13px; }
.plugins-head-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }
.current-machine-chip { display: flex; align-items: baseline; gap: 7px; padding: 6px 10px; border: 1px solid var(--line); border-radius: var(--r-ctrl); background: var(--surface); font-size: 12px; }
.current-machine-chip span { color: var(--ink-sub); }
.current-machine-chip strong { color: var(--ink); }
.current-machine-chip code { color: var(--ink-sub); font-size: 11px; }
.developer-status { padding: 6px 9px; border-radius: var(--r-ctrl); background: var(--surface-2); color: var(--ink-sub); font-size: 12px; white-space: nowrap; }
.developer-status.on { background: var(--accent-soft); color: var(--accent-deep); }
.plugins-error { margin: 0 0 12px; padding: 8px 12px; color: var(--err); background: var(--err-bg); border: 1px solid var(--err-line); border-radius: var(--r-ctrl); }
.plugins-layout { display: grid; grid-template-columns: 230px minmax(0, 1fr); gap: 14px; }
.plugins-machines, .plugins-main { background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-panel); box-shadow: var(--shadow-card); }
.plugins-machines { padding: 12px; }
.plugins-section-title { color: var(--ink); font-size: 14px; font-weight: 700; }
.current-only { margin-top: 10px; padding: 10px; color: var(--ink-sub); background: var(--surface-2); border-radius: var(--r-ctrl); font-size: 12px; line-height: 1.6; }
.machine-row { width: 100%; margin-top: 8px; padding: 9px 10px; display: grid; grid-template-columns: 1fr auto; gap: 3px 8px; text-align: left; border: 1px solid transparent; border-radius: var(--r-ctrl); background: transparent; color: var(--ink-body); cursor: pointer; font-family: inherit; }
.machine-row:hover { background: var(--surface-2); }
.machine-row.selected { border-color: var(--accent); background: var(--accent-soft); }
.machine-name { font-weight: 600; }
.machine-state { font-size: 11px; }
.machine-state.online { color: var(--ok); }
.machine-state.offline { color: var(--ink-sub); }
.machine-row code { grid-column: 1 / -1; color: var(--ink-sub); font-size: 11px; }
.plugins-main { overflow: hidden; }
.plugins-main-head { display: flex; align-items: center; gap: 10px; padding: 13px 16px; border-bottom: 1px solid var(--line); }
.plugins-machine-label { color: var(--ink-sub); font-size: 12px; }
.plugins-table-wrap { overflow: auto; }
.plugins-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.plugins-table th, .plugins-table td { padding: 11px 12px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: middle; }
.plugins-table th { color: var(--ink-sub); font-size: 12px; font-weight: 600; white-space: nowrap; }
.plugin-info { min-width: 190px; }
.plugin-info strong, .plugin-info code, .plugin-info span { display: block; }
.plugin-info code, .mono { font-family: var(--font-mono); font-size: 11px; color: var(--ink-sub); }
.plugin-info span { margin-top: 3px; color: var(--ink-sub); font-size: 12px; }
.plugin-tag { display: inline-block; padding: 3px 8px; border-radius: var(--r-pill); font-size: 12px; white-space: nowrap; }
.plug-enabled { color: var(--ok); background: var(--ok-bg); }
.plug-disabled { color: var(--ink-sub); background: var(--surface-2); }
.plug-uninstalled, .plug-unknown { color: var(--err); background: var(--err-bg); }
.plugin-error { display: block; max-width: 180px; margin-top: 4px; color: var(--err); font-size: 11px; }
.plugin-actions { white-space: nowrap; }
.plugins-empty { padding: 20px 12px; color: var(--ink-sub); text-align: center; font-size: 13px; }
@media (max-width: 760px) { .plugins-layout { grid-template-columns: 1fr; } .plugins-head { align-items: stretch; flex-direction: column; } .plugins-head-actions { justify-content: flex-start; } }
</style>
