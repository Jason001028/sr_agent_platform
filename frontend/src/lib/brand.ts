/**
 * 平台名（2026-09-21 定名）：顶栏品牌区与路由 document.title 共用这一份。
 *
 * 改名时要一并改 `frontend/index.html` 的 <title> —— 那是 JS 起来之前的字面量，
 * import 不进去，改不到（只影响首屏那一瞬与禁用 JS 时）。
 *
 * 顶栏那颗 logo **不在仓库里**（正式素材在内网机上），部署后在服务器上直接换
 * `<APP>/dist/logo.png` 即可，不用重打包 —— 见 deploy/README.md §二。
 */
export const APP_NAME = '长光卫星-修图智能体平台';
