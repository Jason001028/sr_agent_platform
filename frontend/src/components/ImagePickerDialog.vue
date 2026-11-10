<script setup lang="ts">
import { ref, onBeforeUnmount } from 'vue';
import { createPickerSession, pickerState, waitPicker } from '../lib/imageOpen';
import type { PickerState } from '../lib/imageOpen';
import { LoadControl, isLoadAbort } from '../lib/loadControl';
import { useViewerStore } from '../stores/viewer';
const emit=defineEmits<{browse:[]}>();
const store=useViewerStore(), dialog=ref<HTMLDialogElement|null>(null);
const session=ref<Awaited<ReturnType<typeof createPickerSession>>|null>(null);
const pending=ref(false), paused=ref(false), error=ref(''), label=ref('等待选择影像…');
let control:LoadControl|null=null, generation=0;
const installer=`${import.meta.env.BASE_URL}client-explorer/SrExplorer-Setup.zip`;
async function open(){
  if(pending.value)return;
  error.value='';dialog.value?.showModal();
  const current=++generation;
  try{const value=await createPickerSession();if(current===generation)session.value=value;else void pickerState(value.token,'cancelled').catch(()=>{});}
  catch(e){error.value=e instanceof Error?e.message:String(e);}
}
function dismiss(){generation++;dialog.value?.close();if(session.value)void pickerState(session.value.token,'cancelled').catch(()=>{});session.value=null;}
function browse(){dismiss();emit('browse');}
function stop(){control?.stop();store.stopImageLoad();}
function toggle(){if(!control)return;if(control.paused)control.resume();else control.pause();paused.value=control.paused;}
async function launch(event:MouseEvent){
  if(!session.value||pending.value){event.preventDefault();return;}
  const selected=session.value;session.value=null;dialog.value?.close();pending.value=true;error.value='';
  control=new LoadControl();paused.value=false;
  try{
    await waitPicker(selected.token,control,(state:PickerState)=>{
      label.value=state.files.length?state.files.map(file=>`${file.name}：${file.state==='error'?'失败':file.state==='ready'?'服务端 JPG 准备中':Math.floor(file.received/file.size*100)+'% 已上传'}`).join('；'):'等待 Windows 文件选择框；未弹出时请停止并升级组件';
    },asset=>store.openPreviewAsset(asset));
  }catch(e){if(!isLoadAbort(e))error.value=e instanceof Error?e.message:String(e);}
  finally{pending.value=false;control=null;}
}
onBeforeUnmount(()=>{generation++;control?.stop();if(session.value)void pickerState(session.value.token,'cancelled').catch(()=>{});});
defineExpose({open});
</script>
<template>
  <Teleport to="body">
    <dialog ref="dialog" class="image-picker" @cancel.prevent="dismiss" @click.stop @keydown.stop>
      <h3>选择影像 · 服务端 JPG 优先</h3>
      <p>路径组件可读取 W／X 盘、其他盘符和网络共享的真实路径。服务端可直接读取时无需上传；不可直读时分块上传生成临时 JPG。</p>
      <p>首次使用或旧版组件需要下载安装包，解压后双击 Install-SrExplorer.cmd。</p>
      <a :href="installer" download="SrExplorer-Setup.zip">下载／升级 Windows 组件</a>
      <div class="picker-actions">
        <a v-if="session" class="picker-primary" :href="session.uri" @click="launch">已安装，选择影像</a>
        <span v-else>正在准备选择会话…</span>
        <button type="button" @click="browse">普通文件选择（上传预览）</button>
        <button type="button" @click="dismiss">关闭</button>
      </div>
      <p v-if="error" role="alert">{{ error }}</p>
      <p class="picker-note">普通选择器不会提供真实路径。临时 JPG 不代表已关联盘阵，不会因此获得提交 SR 权限。</p>
    </dialog>
    <aside v-if="pending && !store.imageLoad.active" class="picker-progress" aria-live="polite">
      <span>{{ label }}</span><button @click="toggle">{{ paused?'继续':'暂停' }}</button><button @click="stop">停止打开</button>
    </aside>
    <aside v-if="error && !dialog?.open" class="picker-progress" role="alert"><span>{{ error }}</span><button @click="error=''">关闭</button></aside>
  </Teleport>
</template>
<style scoped>
.image-picker{max-width:580px;padding:24px;border:1px solid var(--line);border-radius:14px;background:var(--surface);color:var(--ink)}
.image-picker::backdrop{background:rgba(20,30,25,.3)}.image-picker p{line-height:1.7}.picker-actions{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:18px}
.picker-actions button,.picker-primary,.picker-progress button{padding:7px 12px;border:1px solid var(--line);border-radius:7px;background:var(--surface);color:var(--ink);cursor:pointer;text-decoration:none}.picker-primary{background:var(--accent);color:white}
.picker-note{font-size:12px;color:var(--ink-sub)}.picker-progress{position:fixed;right:20px;bottom:24px;z-index:1100;max-width:620px;display:flex;gap:10px;align-items:center;padding:14px;background:var(--surface);border:1px solid var(--line);border-radius:10px;box-shadow:0 6px 24px #0002}.picker-progress span{overflow-wrap:anywhere}
</style>
