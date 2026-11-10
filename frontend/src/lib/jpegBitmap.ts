import { getPreviewTrueMaxEdge } from './previewLevel';

/** Read JPEG frame dimensions without decoding pixels or copying the full file. */
export async function jpegSize(blob: Blob): Promise<{ width: number; height: number } | null> {
  const read = async (offset: number, length: number) => {
    const part = blob.slice(offset, offset + length);
    const buffer = typeof part.arrayBuffer === 'function' ? await part.arrayBuffer()
      : await new Promise<ArrayBuffer>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result as ArrayBuffer);
        reader.onerror = () => reject(reader.error);
        reader.readAsArrayBuffer(part);
      });
    return new Uint8Array(buffer);
  };
  const soi = await read(0, 2);
  if (soi[0] !== 0xff || soi[1] !== 0xd8) return null;
  let offset = 2;
  while (offset + 4 <= blob.size) {
    const head = await read(offset, 4);
    if (head[0] !== 0xff) return null;
    const marker = head[1]!;
    if (marker === 0xff) { offset++; continue; }
    if (marker === 0xda || marker === 0xd9) return null;
    if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) { offset += 2; continue; }
    const length = head[2]! * 256 + head[3]!;
    if (length < 2 || offset + 2 + length > blob.size) return null;
    if (marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker)) {
      if (length < 8) return null;
      const frame = await read(offset + 4, 5);
      const height = frame[1]! * 256 + frame[2]!;
      const width = frame[3]! * 256 + frame[4]!;
      return width && height ? { width, height } : null;
    }
    offset += 2 + length;
  }
  return null;
}

/** Request the bounded bitmap during decode, before a full-size RGBA allocation.
 *  `maxEdge` 与平台设置里的「真分辨率」上限同口径（默认长边 16384）；超过这个
 *  尺寸浏览器画布本身就不保证能建出来，所以高于它必须 resize。默认值在**调用时**
 *  求值，设置改了以后下一次解码就按新上限走。 */
export async function decodeJpegBitmap(blob: Blob, maxEdge = getPreviewTrueMaxEdge()): Promise<ImageBitmap> {
  const size = await jpegSize(blob);
  const scale = size ? Math.min(1, maxEdge / Math.max(size.width, size.height)) : 1;
  const options: ImageBitmapOptions | undefined = size && scale < 1 ? {
    resizeWidth: Math.max(1, Math.round(size.width * scale)),
    resizeHeight: Math.max(1, Math.round(size.height * scale)),
    resizeQuality: 'high',
  } : undefined;
  try {
    return await createImageBitmap(blob, options);
  } catch (error) {
    const dims = size ? `（${size.width}×${size.height}）` : '';
    throw new Error(`预览图解码失败${dims}，请尝试较低预览档位；${error instanceof Error ? error.message : String(error)}`);
  }
}
