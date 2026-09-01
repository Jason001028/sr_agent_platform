/// <reference types="vite/client" />

// .vue 单文件组件在 TS 里的模块声明（vue-tsc 负责 SFC 内部类型检查，此声明供普通 .ts 引用时用）
declare module '*.vue' {
  import type { DefineComponent } from 'vue';
  const component: DefineComponent<{}, {}, any>;
  export default component;
}

// vendor 三库为 UMD/IIFE 纯 JS（utif.js 为补丁版，绝不 npm 覆盖）。
// 这里声明成 any，避免 TS 在类型层卡住导入；类型行为由 lib 层自行约束。
//
// ⚠️ 不要把这段声明放回 src/vendor/vendor.d.ts：它与 src/vendor/vendor.ts 是「同名兄弟」，
// TS 的 file-inclusion 会以 .ts 为准、丢弃 .d.ts（Phase 2 实测：src/vendor/vendor.d.ts 被挤出 program，
// 导致全部 vendor import 报 TS7016）。环境声明统一收在本文件（src/env.d.ts 无同名 .ts 兄弟，可被 include）。
// 用 `export =`（而非 `export default`）：esbuild(vitest) 对 CJS 给 module.exports 为 default、
// rollup(vite build) 给 exports.default；geotiff UMD 带 `exports.default=GeoTIFF(类)`，
// 两种转换下 default 含义不同。`export =` + esModuleInterop 让 `import X from` 和 `import * as X`
// 都解析成整个导出对象（any），消除构建互操作差异（详见 tifDecode.ts 的 geotiff 导入注释）。
declare module '*.min.js' {
  const value: any;
  export = value;
}
declare module '*/utif.js' {
  const UTIF: any;
  export = UTIF;
}
declare module '*/geotiff.min.js' {
  const GeoTIFF: any;
  export = GeoTIFF;
}
