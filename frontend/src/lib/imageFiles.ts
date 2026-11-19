/**
 * imageFiles.ts — 选文件/拖入的文件分类（最小原型 §4.6）
 * ------------------------------------------------------------------
 * 查看器现在同时接受两类影像，走两条不同的管线：
 *   - TIF/TIFF → 解码管线（tifDecode；全分辨率、稀疏条带/分块）
 *   - JPG/JPEG → 显示就绪图片管线（createImageBitmap，stretch 退化为恒等）
 * 分类只看扩展名 + MIME：浏览器给的 type 可能为空（拖入、部分系统），
 * 扩展名也可能缺失，两边任一命中即算 —— 与旧 TIF 过滤的判定方式一致。
 * 纯函数，无 DOM：便于 vitest 覆盖（store 的 addFiles 直接用它分派）。
 */

export type ImageKind = 'tif' | 'jpg' | 'png';

export interface NamedFileLike {
  name: string;
  type?: string;
}

/** 浏览器读到的 0 字节 File：多半是文件已被删除/移动，而资源管理器还没刷新，
 *  拖出来的只是一个失效条目；同步盘的占位文件也可能这样。此时 File.size 为 0、
 *  读内容全空，后端只会把它当成非法的文件大小。 */
export function isZeroLengthImageFile(file: { size: number }): boolean {
  return !(file.size > 0);
}

/** 入口检查失败时给用户看的话：说清是文件不存在，而不是「格式/大小非法」。 */
export function zeroLengthImageMessage(names: string[]): string {
  return `文件不存在或不可读取：${names.join('、')}（浏览器读到 0 字节）。`
    + '如果文件刚被移动或删除，请刷新资源管理器后重新选择。';
}

/** 单个文件的影像类型；不是受支持的影像 → null。 */
export function imageKindOf(f: NamedFileLike): ImageKind | null {
  const name = f.name || '';
  const mime = f.type || '';
  if (/\.tiff?$/i.test(name) || mime.indexOf('tiff') !== -1) return 'tif';
  if (/\.jpe?g$/i.test(name) || mime.indexOf('jpeg') !== -1) return 'jpg';
  if (/\.png$/i.test(name) || mime === 'image/png') return 'png';
  return null;
}

/** 按类型分堆（顺序保持输入顺序；不支持的格式直接丢弃）。 */
export function classifyImages<T extends NamedFileLike>(files: T[]): { tifs: T[]; imgs: T[] } {
  const tifs: T[] = [];
  const imgs: T[] = [];
  for (const f of files) {
    const kind = imageKindOf(f);
    if (kind === 'tif') tifs.push(f);
    else if (kind === 'jpg'||kind === 'png') imgs.push(f);
  }
  return { tifs, imgs };
}
