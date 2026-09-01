import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

// Vue3 + TS + Vite 构建配置（阶段2）
// - base:'./'：产物用相对路径引用资源，解压到任意 Nginx 根/子目录都能跑（离线交付友好）。
// - vendor 三库（pako→utif→geotiff）经 src/vendor/vendor.ts 以模块导入打进 dist，产物自包含无 CDN。
export default defineConfig({
  plugins: [vue()],
  base: './',
  build: {
    target: 'es2022', // 仅 Chromium 系内网机，直接用现代目标，产物更小
    outDir: 'dist',
    sourcemap: false,
    // Vite 6 的 @rollup/plugin-commonjs 默认只处理 node_modules；src/vendor 三库
    // 是 UMD/IIFE 纯 JS（utif.js 为补丁版），必须按 CJS 处理才有 default 导出。
    commonjsOptions: {
      include: [/node_modules/, /src[\\/]vendor/],
    },
  },
  server: {
    port: 5173,
    open: false,
  },
});
