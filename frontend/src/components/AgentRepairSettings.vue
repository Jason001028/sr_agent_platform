<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { agentRepairSettings as settings } from '../lib/agentRepairSettings';
import LayerSwitch from './LayerSwitch.vue';
import AppIcon from './AppIcon.vue';
const open=ref(false),root=ref<HTMLElement|null>(null),trigger=ref<HTMLButtonElement|null>(null);
const panel=ref<HTMLElement|null>(null),position=ref({left:'0px',top:'0px'});
function toggle(){
  const button=trigger.value;if(!button)return;
  const rect=button.getBoundingClientRect(),anchor=button.closest('.at-head,.cp-head')?.getBoundingClientRect()??rect;
  const width=Math.min(310,window.innerWidth-32);
  position.value={left:Math.max(8,Math.min(window.innerWidth-width-8,anchor.right-width))+'px',top:rect.bottom+7+'px'};
  open.value=!open.value;
}
function outside(e:PointerEvent){if(!root.value?.contains(e.target as Node)&&!panel.value?.contains(e.target as Node))open.value=false;}
function escape(e:KeyboardEvent){if(e.key==='Escape'&&open.value){e.stopPropagation();open.value=false;trigger.value?.focus();}}
onMounted(()=>document.addEventListener('pointerdown',outside));
onBeforeUnmount(()=>document.removeEventListener('pointerdown',outside));
</script>
<template>
  <div ref="root" class="agent-settings" @keydown="escape">
    <button ref="trigger" type="button" class="settings-trigger" aria-label="Agent 修复设置" :aria-expanded="open" @click="toggle"><AppIcon name="settings" :size="16" tint /></button>
    <Teleport to="body"><Transition name="agent-settings"><section v-if="open" ref="panel" class="settings-panel" :style="position" aria-label="Agent 修复设置" @keydown="escape">
      <header><strong>Agent 修复设置</strong><button type="button" aria-label="关闭设置" @click="open=false">×</button></header>
      <button class="setting-row" type="button" role="switch" :aria-checked="settings.inspection_only" @click="settings.inspection_only=!settings.inspection_only">
        <span><b>Agent协助仅质检</b><small>检测与定位，不生成掩码或提交 SR</small></span><LayerSwitch :selected="settings.inspection_only" />
      </button>
      <div class="method-row"><label for="agent-detection-method">伪影 / 模糊定位方式</label><select id="agent-detection-method" v-model="settings.detection_method" aria-label="Agent 检测方法"><option value="absdiff">绝对差异</option><option value="ssim">结构差异 SSIM</option><option value="smart">LTD 纹理退化</option></select></div>
      <button class="setting-row" type="button" role="switch" :aria-checked="settings.tone_weight" @click="settings.tone_weight=!settings.tone_weight">
        <span><b>非线性归因权重</b><small>用于候选排序与热力图，不改变掩码阈值</small></span><LayerSwitch :selected="settings.tone_weight" />
      </button>
      <button class="setting-row" type="button" role="switch" :aria-checked="settings.skip_completed" @click="settings.skip_completed=!settings.skip_completed">
        <span><b>自动跳过已超分景</b><small>已有 PAN 备份的景跳过，保留原文件</small></span><LayerSwitch :selected="settings.skip_completed" />
      </button>
      <footer>自动保存，应用于下一条消息；已有计划保持冻结。</footer>
    </section></Transition></Teleport>
  </div>
</template>
<style scoped>
.agent-settings{position:relative;display:inline-flex;flex:none}.settings-trigger{display:grid;place-items:center;width:28px;height:28px;border:0;border-radius:7px;background:transparent;color:var(--tb-muted);cursor:pointer}.settings-trigger:hover,.settings-trigger[aria-expanded=true]{background:var(--tb-hover);color:var(--tb-fg)}
.settings-panel{position:fixed;z-index:1000;width:310px;max-width:calc(100vw - 32px);max-height:calc(100vh - 110px);overflow:auto;box-sizing:border-box;padding:12px;background:var(--tb-bg);border:1px solid var(--tb-border);border-radius:12px;color:var(--tb-fg);box-shadow:0 4px 18px #173f3b14;font-size:12px;text-align:left}
header{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}header strong{font-weight:600}header button{border:0;background:transparent;color:var(--tb-muted);font-size:18px;cursor:pointer}
.setting-row{display:flex;align-items:center;justify-content:space-between;gap:12px;width:100%;padding:10px 0;border:0;background:transparent;color:inherit;text-align:left;cursor:pointer;font:inherit}.setting-row>span{min-width:0;display:grid;gap:4px}b{font-weight:500}small{color:var(--tb-muted);font-size:10px;line-height:1.5}
.method-row{display:grid;gap:7px;padding:10px 0}select{min-width:0;width:100%;padding:7px 9px;border:1px solid var(--tb-border);border-radius:7px;background:var(--surface,#fff);color:inherit;font:inherit}footer{padding-top:8px;color:var(--tb-muted);font-size:10px;line-height:1.5}
button:focus-visible,select:focus-visible{outline:2px solid var(--tb-focus);outline-offset:2px}.agent-settings-enter-active,.agent-settings-leave-active{transition:opacity 180ms ease,transform 180ms ease}.agent-settings-enter-from,.agent-settings-leave-to{opacity:0;transform:translateY(-3px)}
@media(prefers-reduced-motion:reduce){.agent-settings-enter-active,.agent-settings-leave-active{transition:none}}
</style>
