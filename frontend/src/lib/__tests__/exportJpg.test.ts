/**
 * exportJpg.test.ts — 导出编排 collectForExport 路由 golden（自包含 fixtures）
 * ------------------------------------------------------------------
 * collectForExport 与预览相同路由：小8bit→UTIF；无压缩条带单波段大图→稀疏；其余→分块。
 * Node 覆盖稀疏/分块两路由（不碰 canvas/UTIF，kit 注入抛错桩保证不会误走 UTIF）：
 *   - 稀疏：setSparseMin(0) 让 256×256 小 fixture 走稀疏 → 与直接 sparseCollect 逐像素一致
 *   - 分块：16bit 非稀疏候选（默认 SPARSE_MIN=1e8）→ 与直接 chunkedCollect 逐像素一致
 * UTIF / canvasToJpegBlob / exportToJpg 落盘链路由 .e2e 浏览器回归覆盖。
 */
import { afterEach, describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import {
  probeImage, getSamplePlan, parseStrips, sparseCollect, chunkedCollect,
  setSparseMin,
} from '../tifDecode.js';
import type { CanvasKit, SparseLayoutOk } from '../tifDecode.js';
import { FileSource } from '../source.js';
import { collectForExport } from '../exportJpg.js';

function fixtureBuf(name: string): Uint8Array<ArrayBuffer> {
  const raw = readFileSync(new URL(`../../../fixtures/${name}`, import.meta.url));
  const out = new Uint8Array(raw.byteLength);
  out.set(raw);
  return out;
}
function fixtureSource(name: string): FileSource {
  return new FileSource(new Blob([fixtureBuf(name)]), name);
}
function fixtureFile(name: string): File {
  return new File([fixtureBuf(name)], name);
}

// 非 UTIF 路由不应触碰 canvas —— 注入抛错桩，误走即爆
const stubKit: CanvasKit = {
  createCanvas: () => {
    throw new Error('kit.createCanvas 不应被调用（本用例走稀疏/分块路由）');
  },
  createImageData: () => {
    throw new Error('kit.createImageData 不应被调用');
  },
};

afterEach(() => setSparseMin(1e8));

describe('collectForExport 路由分派（稀疏 / 分块）', () => {
  it('无压缩条带单波段图 → 稀疏（setSparseMin 钩子降低阈值）', async () => {
    setSparseMin(0);   // 256×256×2B 的小 fixture 本不够 1e8，钩子让它走稀疏
    const source = fixtureSource('gray16_grad.tif');
    const probe = await probeImage(source.blob);
    const rec = { file: fixtureFile('gray16_grad.tif'), probe, exportCap: 128 };

    const r = await collectForExport(rec, source, stubKit);

    expect(r.nb).toBe(1);
    expect(r.W).toBe(256);
    expect(r.H).toBe(256);
    expect(r.sw).toBe(128);
    expect(r.sh).toBe(128);
    expect(r.plan.longEdge).toBe(128);
    expect(r.plan.reduced).toBe(false);

    // 与直接 sparseCollect 同参结果逐像素一致（证明路由走对）
    const sp = await parseStrips(source);
    expect(sp.ok).toBe(true);
    const direct = await sparseCollect(source, probe, sp as SparseLayoutOk, 128);
    expect(r.invert).toBe(probe.photometric === 0);
    expect(Array.from(r.src)).toEqual(Array.from(direct.src));
  });

  it('16bit 非稀疏候选 → 分块（chunkedCollect 语义）', async () => {
    const source = fixtureSource('gray16_grad.tif');   // 默认 SPARSE_MIN=1e8 → 不走稀疏
    const probe = await probeImage(source.blob);
    const rec = { file: fixtureFile('gray16_grad.tif'), probe, exportCap: 128 };

    const r = await collectForExport(rec, source, stubKit);

    expect(r.nb).toBe(1);
    expect(r.W).toBe(256);
    expect(r.H).toBe(256);
    expect(r.sw).toBe(128);
    expect(r.sh).toBe(128);

    // 与直接 chunkedCollect 同参结果逐像素一致（证明路由走对）
    const plan2 = getSamplePlan(probe.image);
    const direct = await chunkedCollect(probe.image, 256, 256, plan2, r.plan.scale, 128, 128);
    expect(r.invert).toBe(plan2.invert);
    expect(Array.from(r.src)).toEqual(Array.from(direct));
  });
});
