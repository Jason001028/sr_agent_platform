/**
 * saver.ts — 落盘层（只余浏览器下载）
 * ------------------------------------------------------------------
 * 只余 downloadBlob（浏览器下载），供掩码导出（exportMaskJson / genMask）使用。
 * 前端不落盘：中间产物由 SR 自己在盘阵原图目录旁生成 —— tif-viewer.html 那套
 * File System Access 落盘层（fsIO / 输出目录授权 / 按日期写 JPG 中间产物）不在其中。
 */

/** 浏览器下载 Blob（HTML downloadBlob 直译） */
export function downloadBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
