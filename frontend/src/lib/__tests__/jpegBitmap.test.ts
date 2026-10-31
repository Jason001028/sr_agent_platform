import { afterEach, expect, it, vi } from 'vitest';
import { decodeJpegBitmap, jpegSize } from '../jpegBitmap';

afterEach(() => vi.unstubAllGlobals());
function jpeg(width: number, height: number, marker = 0xc0) {
  return new Blob([new Uint8Array([
    0xff, 0xd8, 0xff, 0xe1, 0, 4, 10, 20,
    0xff, marker, 0, 8, 8, height >> 8, height & 255, width >> 8, width & 255, 1,
  ])]);
}
it.each([0xc0, 0xc2])('requests bounded decode for a 30000px JPEG (SOF %s)', async marker => {
  const bitmap = { close() {} };
  const decode = vi.fn().mockResolvedValue(bitmap);
  vi.stubGlobal('createImageBitmap', decode);
  const blob = jpeg(30000, 20000, marker);
  expect(await jpegSize(blob)).toEqual({ width: 30000, height: 20000 });
  expect(await decodeJpegBitmap(blob)).toBe(bitmap);
  expect(decode).toHaveBeenCalledWith(blob, {
    resizeWidth: 4096, resizeHeight: 2731, resizeQuality: 'high',
  });
});
it('keeps small images unchanged and rejects truncated frame headers', async () => {
  vi.stubGlobal('createImageBitmap', vi.fn().mockResolvedValue({}));
  const blob = jpeg(100, 200);
  await decodeJpegBitmap(blob);
  expect(createImageBitmap).toHaveBeenCalledWith(blob, undefined);
  expect(await jpegSize(blob.slice(0, 15))).toBeNull();
  expect(await jpegSize(new Blob(['not jpeg']))).toBeNull();
});
it('includes source dimensions on decode failure without retrying full-size', async () => {
  const decode = vi.fn().mockRejectedValue(new Error('could not be decoded'));
  vi.stubGlobal('createImageBitmap', decode);
  await expect(decodeJpegBitmap(jpeg(30000, 20000))).rejects.toThrow('30000×20000');
  expect(decode).toHaveBeenCalledTimes(1);
});
