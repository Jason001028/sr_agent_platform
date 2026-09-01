/**
 * browserKit.ts — 真实浏览器 Canvas 依赖注入（tifDecode.buildThumb / decode / 导出用）
 * ------------------------------------------------------------------
 * tifDecode.ts 的 buildThumb 等 canvas 函数走 CanvasKit 依赖注入（Node 测试不覆盖）。
 * 浏览器端由本文件注入真实实现：createCanvas → document.createElement('canvas')，
 * createImageData → new ImageData(...)。返回真实 HTMLCanvasElement / ImageData，
 * 结构性满足 KitCanvas/KitImageData 接口，同时保留 toBlob/getImageData 等浏览器能力。
 */
import type { CanvasKit, KitCanvas } from './tifDecode.js';

/** 创建真实 canvas（w×h，浏览器端唯一实现） */
export function createCanvas(w: number, h: number): HTMLCanvasElement {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  return c;
}

/** 创建真实 ImageData（背板 ArrayBuffer 由 Uint8ClampedArray 提供） */
export function createImageData(data: Uint8ClampedArray, w: number, h: number): ImageData {
  // stretchRgba/buildThumb 产出的 Uint8ClampedArray 总是新建 ArrayBuffer 背板，故安全窄化。
  return new ImageData(data as Uint8ClampedArray<ArrayBuffer>, w, h);
}

// KitCanvas 接口的 drawImage 入参是 KitCanvas（结构性），真实 canvas 的 drawImage 入参是
// CanvasImageSource —— 两接口互不满足，运行时对象却是同一 HTMLCanvasElement。安全窄化：
// 真实 canvas 同时满足两者（结构上窄化一个方向），调用方需要 toBlob/getImageData 时再窄化回。
const browserKitRaw: CanvasKit = {
  createCanvas: (w: number, h: number) => createCanvas(w, h) as unknown as KitCanvas,
  createImageData,
};

/** 浏览器 CanvasKit 单例：注入 buildThumb / decode / 导出编排 */
export const browserKit: CanvasKit = browserKitRaw;
