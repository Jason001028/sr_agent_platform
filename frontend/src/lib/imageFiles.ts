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

export type ImageKind = 'tif' | 'jpg';

export interface NamedFileLike {
  name: string;
  type?: string;
}

/** 单个文件的影像类型；不是受支持的影像 → null。 */
export function imageKindOf(f: NamedFileLike): ImageKind | null {
  const name = f.name || '';
  const mime = f.type || '';
  if (/\.tiff?$/i.test(name) || mime.indexOf('tiff') !== -1) return 'tif';
  if (/\.jpe?g$/i.test(name) || mime.indexOf('jpeg') !== -1) return 'jpg';
  return null;
}

/** 按类型分堆（顺序保持输入顺序；不支持的格式直接丢弃）。 */
export function classifyImages<T extends NamedFileLike>(files: T[]): { tifs: T[]; imgs: T[] } {
  const tifs: T[] = [];
  const imgs: T[] = [];
  for (const f of files) {
    const kind = imageKindOf(f);
    if (kind === 'tif') tifs.push(f);
    else if (kind === 'jpg') imgs.push(f);
  }
  return { tifs, imgs };
}
