/**
 * 离线 vendor 接线入口（阶段2 · 产物自包含）
 * ------------------------------------------------------------------
 * 引入顺序固定：pako → utif（补丁版）→ geotiff，与 tif-viewer.html 的
 * `<script src="vendor/pako.min.js"></script>` 三连保持一致。
 *
 * - pako.min.js    UMD 2.1.0（npm 同步版本，非补丁）
 * - utif.js        补丁版（cmpr 8/32946 走 pako inflate）——绝不能被 npm 重装覆盖，
 *                  必须从本地 vendor 文件引入；decodeUtif 在 Phase 3 才用到，此处先接线进包。
 * - geotiff.min.js geotiff@3.0.5（命名空间导入，见 tifDecode.ts 同款注释；re-export 是完整命名空间，含 fromBlob）
 *
 * 产物经 Vite 打进 dist，全离线无 CDN 引用。
 */
import './globals'; // ① pako（先求值，window.pako 就位，utif 依赖它）
import UTIF from './utif.js'; // ② 补丁版 utif
import * as GeoTIFF from './geotiff.min.js'; // ③ geotiff（命名空间，勿改 default：见 tifDecode.ts 注释）

export { pako } from './globals';
export { UTIF };
export { GeoTIFF };
