// vendor 三库为 UMD/IIFE 纯 JS（utif.js 为补丁版，绝不 npm 覆盖）。
// 这里声明成 any，避免 TS 在类型层卡住导入；类型行为由 lib 层自行约束。
declare module '*.min.js' {
  const value: any;
  export default value;
}
declare module '*/utif.js' {
  const UTIF: any;
  export default UTIF;
}
declare module '*/geotiff.min.js' {
  const GeoTIFF: any;
  export default GeoTIFF;
}
