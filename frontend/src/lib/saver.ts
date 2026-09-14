/**
 * saver.ts — 落盘层（现只余浏览器下载）
 * ------------------------------------------------------------------
 * 原为 tif-viewer.html 的 File System Access 落盘层（fsIO / 输出目录授权 /
 * 按日期写 JPG 中间产物）。最小原型取消了「浏览器导出 JPG」这条链路：
 * 中间产物由 SR 自己在盘阵原图目录旁生成，前端不再落盘，故整层删去，
 * 只保留仍被掩码导出（exportMaskJson / genMask）使用的 downloadBlob。
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
