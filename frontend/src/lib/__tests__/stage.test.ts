/**
 * lib/stage.ts —— 环节标签与修复拒绝语。
 *
 * 这里钉的是**用户口径**（2026-09-21 那条需求）：同一景的不同环节产物要用简短标志
 * 区分开，取值 `PAN` / `SR` / `NOSR`。三处拒绝入口（`enterDraw` /
 * `bakeMaskToServer` / `submitSr`）共用同一句话，所以只在这里验一次文案。
 */
import { describe, expect, it } from 'vitest';
import { isIntermediateStage, stageLabel, stageRefusal } from '../stage.js';

const SC = 'A_B_20260921_124710_200536960_101_0005_001';

describe('isIntermediateStage', () => {
  it('只有 product 与 nosr 不能修复', () => {
    expect(isIntermediateStage('product')).toBe(true);
    expect(isIntermediateStage('nosr')).toBe(true);
    expect(isIntermediateStage('input')).toBe(false);
  });

  it('缺省（老 rec / 本地图片）按本体算 —— 它们另有 lqPath 那道门', () => {
    expect(isIntermediateStage(undefined)).toBe(false);
    expect(isIntermediateStage(null)).toBe(false);
  });
});

describe('stageLabel', () => {
  it('本轮超分产物用后端给的 suffix 大写', () => {
    expect(stageLabel('product', 'sr', SC + '_sr.jpg')).toBe('SR');
  });

  it('后端没给 suffix 时从文件名末尾一段兜底', () => {
    expect(stageLabel('product', null, SC + '_sr.jpg')).toBe('SR');
    expect(stageLabel('product', '', SC + '_sr_2.jpg')).toBe('2');
  });

  it('未超分产物恒标 NOSR（它没有属于自己的 suffix）', () => {
    expect(stageLabel('nosr', 'sr', SC + '_sr_NOSR.jpg')).toBe('NOSR');
  });

  it('本体：RC 场景的 PAN.tif 标 PAN，SC 场景的生产全名标「本体」', () => {
    expect(stageLabel('input', '', 'PAN.tif')).toBe('PAN');
    expect(stageLabel('input', '', 'pan.jpg')).toBe('PAN');
    expect(stageLabel('input', '', SC + '.jpg')).toBe('本体');
  });

  it('缺省 kind 按本体算（与 isIntermediateStage 同一口径）', () => {
    expect(stageLabel(undefined, undefined, 'PAN.tif')).toBe('PAN');
    expect(stageLabel(null, null, SC + '.jpg')).toBe('本体');
  });
});

describe('stageRefusal', () => {
  it('说清是哪一环、为什么不能修、以及该怎么做', () => {
    const msg = stageRefusal('SR');
    expect(msg).toContain('SR');
    expect(msg).toContain('仅用于对比，不作修复');
    expect(msg).toContain('请先打开本体');
  });
});
