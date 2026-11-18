import { ref, watch } from 'vue';
export interface AgentRepairSettings { inspection_only: boolean; detection_method: 'absdiff' | 'ssim' | 'smart'; tone_weight: boolean; skip_completed: boolean; }
export const AGENT_REPAIR_SETTINGS_KEY = 'sr.agent.repair-settings.v1';
export const DEFAULT_AGENT_REPAIR_SETTINGS: AgentRepairSettings = {inspection_only:false,detection_method:'ssim',tone_weight:true,skip_completed:true};
export function normalizeAgentRepairSettings(value: Partial<AgentRepairSettings> = {}): AgentRepairSettings {
  const defaults=DEFAULT_AGENT_REPAIR_SETTINGS;
  return {inspection_only:typeof value.inspection_only==='boolean'?value.inspection_only:defaults.inspection_only,
    detection_method:['absdiff','ssim','smart'].includes(value.detection_method??'')?value.detection_method!:defaults.detection_method,
    tone_weight:typeof value.tone_weight==='boolean'?value.tone_weight:defaults.tone_weight,
    skip_completed:typeof value.skip_completed==='boolean'?value.skip_completed:defaults.skip_completed};
}
function load(){try{return normalizeAgentRepairSettings(JSON.parse(localStorage.getItem(AGENT_REPAIR_SETTINGS_KEY)||'{}')??{});}catch{return {...DEFAULT_AGENT_REPAIR_SETTINGS};}}
export const agentRepairSettings=ref(load());
watch(agentRepairSettings,value=>{try{localStorage.setItem(AGENT_REPAIR_SETTINGS_KEY,JSON.stringify(value));}catch{/* Keep current tab settings. */}},{deep:true,flush:'sync'});
