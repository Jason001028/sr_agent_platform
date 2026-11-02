<script setup lang="ts">
import { computed, ref, useId } from 'vue';
import { explorerUri, EXPLORER_READY_KEY } from '../lib/explorer';

const props = defineProps<{ directory?: string | null }>();
const dialog = ref<HTMLDialogElement | null>(null);
const pathInput = ref<HTMLInputElement | null>(null);
const copied = ref(false);
const titleId = useId();
const uri = computed(() => {
  try { return props.directory ? explorerUri(props.directory) : ''; }
  catch { return ''; }
});
const installer = `${import.meta.env.BASE_URL}client-explorer/SrExplorer-Setup.zip`;
const title = computed(() => uri.value
  ? '在资源管理器中打开（Shift 点击或右键：设置与目录路径）'
  : '请先关联服务端目录，再在资源管理器中打开');

function settings() {
  if (!uri.value) return;
  copied.value = false;
  dialog.value?.showModal();
}
function open(event: MouseEvent) {
  let ready = false;
  try { ready = localStorage.getItem(EXPLORER_READY_KEY) === '1'; } catch { /* use setup */ }
  if (event.shiftKey || !ready) { settings(); return; }
  // A synchronous user gesture is required for an external protocol launch.
  window.location.href = uri.value;
}
function remember() {
  try { localStorage.setItem(EXPLORER_READY_KEY, '1'); } catch { /* setup remains available */ }
  dialog.value?.close();
}
async function copyPath() {
  const input = pathInput.value;
  if (!input) return;
  input.focus(); input.select();
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(input.value);
    else if (!document.execCommand('copy')) return;
    copied.value = true;
  } catch { /* selection allows Ctrl+C on HTTP and restricted browsers */ }
}
</script>

<template>
  <button type="button" class="explorer-open" :disabled="!uri" :title="title"
    aria-label="在资源管理器中打开" data-e2e="file-explorer-open"
    @click.stop="open" @dblclick.stop @contextmenu.stop.prevent="settings">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M3 7V5a1 1 0 0 1 1-1h5l2 3h9a1 1 0 0 1 1 1v2M3 7v12a1 1 0 0 0 1 1h15a1 1 0 0 0 1-.8l2-8a1 1 0 0 0-1-1H7l-4 9" />
    </svg>
  </button>
  <Teleport to="body">
    <dialog ref="dialog" class="explorer-dialog" :aria-labelledby="titleId"
      @click.stop @keydown.stop @click.self="dialog?.close()">
      <h3 :id="titleId">在资源管理器中打开</h3>
      <p>公司统一使用 W 盘，双击安装一次，以后点图标即可打开目录。</p>
      <a class="explorer-download" :href="installer" download="SrExplorer-Setup.zip">下载 Windows 安装包</a>
      <ol class="explorer-steps">
        <li>下载后<strong>解压</strong>安装包。</li>
        <li>双击 <strong>Install-SrExplorer.cmd</strong>，自动安装，无需输入或选择盘符。</li>
        <li>回到这里，点击下方<strong>“已安装，打开目录”</strong>。</li>
      </ol>
      <p class="explorer-hint">无需管理员权限。新版安装包支持后台打开目录、不闪命令窗口，并配置公司内网站点的 Chrome 免提示规则；旧版用户请重新安装一次。安装成功后窗口自动关闭。</p>
      <details class="explorer-advanced">
        <summary>高级配置与手动安装</summary>
        <p>默认 /DiskArray → W:\。解压后也可在安装包文件夹的 PowerShell 中运行：</p>
        <code>powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-SrExplorer.ps1</code>
        <p>使用其他盘符可加 <code>-WindowsRoot 'Z:\'</code>；服务器根目录不同可加 <code>-ServerRoot '/实际挂载根'</code>。</p>
        <p>若 Chrome 仍提示打开 PowerShell：访问 <code>chrome://policy</code>，点击“重新加载政策”，确认 <code>AutoLaunchProtocolsFromOrigins</code> 状态正常。仅放行 <code>http://10.10.81.135:80</code> 调用目录组件，外网测试地址不会放行；公司策略覆盖时请联系 IT。</p>
      </details>
      <label>服务端目录<input ref="pathInput" :value="directory" readonly @focus="pathInput?.select()" /></label>
      <p class="explorer-hint">没有打开？先确认安装完成、盘符可访问。以后可 Shift 点击或右键图标重新查看设置。</p>
      <div class="explorer-actions">
        <button type="button" @click="copyPath">{{ copied ? '已复制' : '复制路径' }}</button>
        <button type="button" @click="dialog?.close()">关闭</button>
        <a :href="uri" @click="remember">已安装，打开目录</a>
      </div>
    </dialog>
  </Teleport>
</template>

<style scoped>
.explorer-open { position:absolute;top:3px;right:27px;z-index:1;display:grid;place-items:center;width:24px;height:22px;padding:0;border:1px solid #1e7b781f;border-radius:5px;background:var(--accent-soft);color:var(--accent-deep);cursor:pointer;transition:color var(--motion-hover),background var(--motion-hover); }
.explorer-open:hover,.explorer-open:focus-visible { color:var(--accent-deep);background:#cfe8e2;border-color:#1e7b7855; }
.explorer-open:focus-visible { outline:1px solid var(--ink-sub);outline-offset:1px; }
.explorer-open:disabled { opacity:.35;cursor:default;background:transparent;border-color:transparent;color:var(--ink-faint); }
.explorer-dialog { width:min(480px,calc(100vw - 48px));padding:22px;border:1px solid var(--line);border-radius:12px;background:var(--surface);color:var(--ink);box-shadow:0 12px 48px #0003; }
.explorer-dialog::backdrop { background:#0005; }
.explorer-dialog h3 { margin:0 0 12px;font-size:16px; }
.explorer-dialog p,.explorer-dialog li { font-size:13px;line-height:1.7; }
.explorer-dialog ol { padding-left:20px; }
.explorer-advanced>code { display:block;padding:10px;background:var(--surface-2);font-size:12px;overflow-wrap:anywhere;white-space:pre-wrap;user-select:text; }
.explorer-dialog a { color:var(--accent-deep); }
.explorer-dialog .explorer-download { display:inline-flex;padding:9px 15px;border-radius:6px;background:var(--accent-deep);color:#fff;font-size:13px;font-weight:600;text-decoration:none; }
.explorer-steps { margin:16px 0;display:grid;gap:8px; }
.explorer-advanced { margin:14px 0;font-size:12px;color:var(--ink-sub); }
.explorer-advanced summary { cursor:pointer;color:var(--accent-deep); }
.explorer-dialog strong { overflow-wrap:anywhere; }
.explorer-dialog label { display:block;font-size:12px; }
.explorer-dialog input { box-sizing:border-box;width:100%;margin-top:6px;padding:8px;border:1px solid var(--line);border-radius:5px;background:var(--surface-2);color:inherit;font-size:12px; }
.explorer-hint { color:var(--ink-sub); }
.explorer-actions { display:flex;justify-content:flex-end;align-items:center;gap:12px;margin-top:16px;font-size:13px; }
.explorer-actions button { padding:5px 9px;border:1px solid var(--line);border-radius:5px;color:inherit;background:transparent;cursor:pointer; }
.explorer-actions a { padding:6px 10px;border-radius:5px;background:var(--accent-soft);text-decoration:none; }
</style>
