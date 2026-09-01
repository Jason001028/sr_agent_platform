import { createApp } from 'vue';
import { createPinia } from 'pinia';
import App from './App.vue';
import router from './router';
// 离线 vendor 接线：pako → utif（补丁版）→ geotiff，固定顺序打进产物（自包含无 CDN）
import './vendor/vendor';
import './style.css';

createApp(App).use(createPinia()).use(router).mount('#app');
