// @vitest-environment jsdom
import { beforeEach,afterEach,expect,it } from 'vitest';
import {createApp,nextTick,type App} from 'vue';
import Settings from '../AgentRepairSettings.vue';
import {agentRepairSettings,DEFAULT_AGENT_REPAIR_SETTINGS,AGENT_REPAIR_SETTINGS_KEY} from '../../lib/agentRepairSettings';
import {buildAgentRepairPrompt,AGENT_INSPECTION_PROMPT} from '../../lib/repairPrompt';
let app:App,root:HTMLDivElement;
beforeEach(()=>{agentRepairSettings.value={...DEFAULT_AGENT_REPAIR_SETTINGS};root=document.createElement('div');document.body.append(root);app=createApp(Settings);app.mount(root);});
afterEach(()=>{app.unmount();root.remove();document.querySelectorAll('.settings-panel').forEach(el=>el.remove());localStorage.removeItem(AGENT_REPAIR_SETTINGS_KEY);});
it('opens one compact panel, saves switches and chooses the inspection instruction',async()=>{
 root.querySelector<HTMLButtonElement>('button')!.click();await nextTick();
 const panel=document.querySelector<HTMLElement>('.settings-panel')!;
 expect(panel.querySelectorAll('[role=switch]')).toHaveLength(3);
 panel.querySelector<HTMLButtonElement>('[role=switch]')!.click();await nextTick();
 expect(agentRepairSettings.value.inspection_only).toBe(true);
 expect(JSON.parse(localStorage.getItem(AGENT_REPAIR_SETTINGS_KEY)!).inspection_only).toBe(true);
 expect(buildAgentRepairPrompt()).toContain(AGENT_INSPECTION_PROMPT);
 expect(buildAgentRepairPrompt()).not.toContain('prepare_repair');
 const method=panel.querySelector('select')!;method.value='smart';method.dispatchEvent(new Event('change'));await nextTick();
 expect(agentRepairSettings.value.detection_method).toBe('smart');
 expect(buildAgentRepairPrompt()).toContain('smart');
 panel.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await nextTick();
 expect(root.querySelector('button')!.getAttribute('aria-expanded')).toBe('false');
});
it('keeps the teleported panel open for its own controls and closes on outside clicks',async()=>{
 root.querySelector<HTMLButtonElement>('button')!.click();await nextTick();
 const panel=document.querySelector<HTMLElement>('.settings-panel')!;
 panel.dispatchEvent(new Event('pointerdown',{bubbles:true}));await nextTick();
 expect(root.querySelector('button')!.getAttribute('aria-expanded')).toBe('true');
 document.body.dispatchEvent(new Event('pointerdown',{bubbles:true}));await nextTick();
 expect(root.querySelector('button')!.getAttribute('aria-expanded')).toBe('false');
});
