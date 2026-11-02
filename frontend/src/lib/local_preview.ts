/** Bound browser preview buffers independently of source size and selected level. */
export function localPreviewSize(w: number, h: number, bands: number, div: number) {
  const maxPixels = 256 * 1024 * 1024 / (4 * Math.max(1, bands));
  const scale = Math.min(1 / div, 8192 / Math.max(w, h), Math.sqrt(maxPixels / (w * h)));
  return { w: Math.max(1, Math.floor(w * scale)), h: Math.max(1, Math.floor(h * scale)) };
}

export async function sourceFingerprint(file: File) {
  const length = 4096;
  const offsets = [0, Math.max(0, Math.floor((file.size - length) / 2)), Math.max(0, file.size - length)];
  const samples = await Promise.all(offsets.map(async offset => {
    const bytes = new Uint8Array(await file.slice(offset, offset + length).arrayBuffer());
    return btoa(String.fromCharCode(...bytes));
  }));
  return { name: file.name, size: file.size, samples };
}
