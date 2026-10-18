import { defineStore } from 'pinia';
import { ref } from 'vue';
import { loadSrConfig } from '../lib/scene.js';
import {
  apiListMachines, apiListPlugins, apiPluginAction,
} from '../lib/api.js';
import { isDeveloperMode } from '../lib/developerMode.js';
import type { ManagedMachine, ManagedPlugin, PluginOperation } from '../lib/api.js';

export const usePluginStore = defineStore('plugins', () => {
  const machines = ref<ManagedMachine[]>([]);
  const plugins = ref<ManagedPlugin[]>([]);
  const selectedMachineId = ref('local');
  const currentMachineId = ref('local');
  const developerMode = ref(false);
  const loading = ref(false);
  const error = ref('');
  const pending = ref<string | null>(null);

  async function loadMachines(): Promise<void> {
    const cfg = loadSrConfig();
    developerMode.value = isDeveloperMode(currentMachineId.value);
    const directory = await apiListMachines(cfg, developerMode.value);
    currentMachineId.value = directory.current_machine_id;
    machines.value = directory.machines;
    if (!machines.value.some((m) => m.machine_id === selectedMachineId.value)
        || (!developerMode.value && selectedMachineId.value !== currentMachineId.value)) {
      selectedMachineId.value = developerMode.value
        ? (machines.value[0]?.machine_id ?? currentMachineId.value)
        : currentMachineId.value;
    }
  }

  async function loadPlugins(): Promise<void> {
    const cfg = loadSrConfig();
    plugins.value = await apiListPlugins(
      cfg, selectedMachineId.value, developerMode.value);
  }

  async function refresh(): Promise<void> {
    loading.value = true;
    error.value = '';
    try {
      await loadMachines();
      await loadPlugins();
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
    } finally {
      loading.value = false;
    }
  }

  async function selectMachine(machineId: string): Promise<void> {
    if (!developerMode.value && machineId !== currentMachineId.value) return;
    selectedMachineId.value = machineId;
    error.value = '';
    try {
      await loadPlugins();
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
    }
  }

  async function action(pluginId: string, actionName: PluginOperation['action']): Promise<void> {
    if (!developerMode.value && selectedMachineId.value !== currentMachineId.value) {
      error.value = '只有开发者模式可以操作其他机器';
      return;
    }
    pending.value = pluginId;
    error.value = '';
    try {
      const cfg = loadSrConfig();
      await apiPluginAction(
        cfg, selectedMachineId.value, pluginId, actionName, developerMode.value);
      await loadPlugins();
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
    } finally {
      pending.value = null;
    }
  }

  return {
    machines, plugins, selectedMachineId, currentMachineId, developerMode,
    loading, error, pending, refresh, loadPlugins, selectMachine, action,
  };
});
