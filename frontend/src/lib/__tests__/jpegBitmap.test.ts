import { afterEach, expect, it, vi } from 'vitest';
import { decodeJpegBitmap, jpegSize } from '../jpegBitmap';
import { applyPreviewTrueMaxEdge, PREVIEW_TRUE_MAX_EDGE } from '../previewLevel';

afterEach(() => {
  vi.unstubAllGlobals();
  applyPreviewTrueMaxEdge(PREVIEW_TRUE_MAX_EDGE);
});
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
  // 默认上限 32768：长边 30000 不超，但整幅 600M 像素超过 16384² 画布面积，
  // 必须按面积缩到约 20066×13377 再解码。
  expect(decode).toHaveBeenCalledWith(blob, {
    resizeWidth: 20066, resizeHeight: 13377, resizeQuality: 'high',
  });
});
it('keeps the 32768 edge but downsamples images over the canvas area', async () => {
  applyPreviewTrueMaxEdge(32768);
  const decode = vi.fn().mockResolvedValue({ close() {} });
  vi.stubGlobal('createImageBitmap', decode);

  // 长边 32768 且面积恰为 16384²：保持原尺寸。
  const exact = jpeg(32768, 8192);
  await decodeJpegBitmap(exact);
  expect(decode).toHaveBeenLastCalledWith(exact, undefined);

  // 长边虽在 32768 内，面积超过画布上限：必须缩小。
  const oversized = jpeg(30000, 20000);
  await decodeJpegBitmap(oversized);
  const options = decode.mock.calls.at(-1)![1] as { resizeWidth: number; resizeHeight: number };
  const scale = Math.sqrt((16384 * 16384) / (30000 * 20000));
  expect(options.resizeWidth).toBe(Math.round(30000 * scale));
  expect(options.resizeHeight).toBe(Math.round(20000 * scale));
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
