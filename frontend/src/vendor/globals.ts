/**
 * vendor 全局接线（必须在 utif.js 之前执行）
 * ------------------------------------------------------------------
 * utif.js（补丁版）在非 Node 环境从 `self.pako` 取解压库：
 *   若 `typeof require == "function"` 走 require("pako")，否则 `pako = self.pako`。
 * Vite 打包后 utif.js 被 CJS 包装，其 require 分支可能解析到 npm pako（重复打包）或未定义；
 * 这里先把 pako 显式挂到 window，保证 utif 一定能取到，且顺序固定（本模块先于 utif.js 求值）。
 */
import pako from './pako.min.js';

export { pako };

declare global {
  interface Window {
    pako: typeof pako;
  }
}

window.pako = pako;
