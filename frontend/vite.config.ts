import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
// @ts-ignore Local build plugin (Node ESM), no browser runtime dependency.
import { explorerSetupPlugin } from './scripts/explorer-package.mjs';

// Vue3 + TS + Vite 构建配置（阶段2）
// - 根路径资源支持 /repair-reports/:id 等多层 history 路由刷新。
// - vendor 三库（pako→utif→geotiff）经 src/vendor/vendor.ts 以模块导入打进 dist，产物自包含无 CDN。
export default defineConfig({
  plugins: [vue(), explorerSetupPlugin()],
  base: '/',
  build: {
    target: 'chrome96', // 为内网 Chrome 96 编译语法，运行时 API 由兼容代码处理
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
