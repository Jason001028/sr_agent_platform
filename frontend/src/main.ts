import { createApp } from 'vue';
import { createPinia } from 'pinia';
import App from './App.vue';
import router from './router';
// 旧内核没有 AbortSignal.timeout / any；必须在任何请求发出之前补上（见 signals.ts）
import { installAbortSignalPolyfills } from './lib/signals';
import { apiGetSettings, loadSrConfig } from './lib/api';
import { applyPreviewTrueMaxEdge } from './lib/previewLevel';
installAbortSignalPolyfills();
// 离线 vendor 接线：pako → utif（补丁版）→ geotiff，固定顺序打进产物（自包含无 CDN）
import './vendor/vendor';
import './style.css';

createApp(App).use(createPinia()).use(router).mount('#app');

/** 平台级设置（真分辨率长边上限）存在服务端，所有页面共用一份。
 *  查看器页自己会在挂载/回焦时同步（并重载可见影像）；这里先拉一次，是为了
 *  让场景库/结果缩略图这些**不走查看器 store** 的页面也用同一个上限做缓存复核。
 *  读不到就保持默认值，等查看器页重试。 */
void (async () => {
  try {
    const body = await apiGetSettings(loadSrConfig());
    applyPreviewTrueMaxEdge(Number(body?.settings?.preview_max_edge));
  } catch { /* 读不到不影响使用 */ }
})();
