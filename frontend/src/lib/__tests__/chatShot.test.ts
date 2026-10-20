/**
 * chatShot.test.ts — 聊天截图的裁剪几何（纯函数）
 * ------------------------------------------------------------------
 * 只测 `planShot`：它吃数字、吐源矩形与目标尺寸，不碰 canvas/DOM。
 * 落笔那一半（drawShot）在浏览器里由 .e2e 覆盖。
 */
import { describe, it, expect } from 'vitest';
import { planShot, SHOT_MAX_SIDE } from '../chatShot.js';

describe('planShot（可见画面 → 源矩形）', () => {
  it('1:1 缩放、左上角偏移 → 只取视口覆盖的那块缩略图像素', () => {
    const plan = planShot({ scale: 1, ox: -300, oy: -400 }, 200, 200, 1000, 1000);
    expect(plan).toEqual({ sx: 300, sy: 400, sw: 200, sh: 200, dw: 200, dh: 200 });
  });

  it('适配视图（整幅都在视口里）→ 整幅', () => {
    // scale 0.2、居中：1000² 缩略图在 300² 视口里占 200²
    const plan = planShot({ scale: 0.2, ox: 50, oy: 50 }, 300, 300, 1000, 1000);
    expect(plan).toEqual({ sx: 0, sy: 0, sw: 1000, sh: 1000, dw: 1000, dh: 1000 });
  });

  it('view = null（这张图没上屏）→ 整幅', () => {
    const plan = planShot(null, 0, 0, 640, 480);
    expect(plan).toEqual({ sx: 0, sy: 0, sw: 640, sh: 480, dw: 640, dh: 480 });
  });

  it('视口尺寸为 0（画布未挂）→ 整幅而不是空矩形', () => {
    const plan = planShot({ scale: 4, ox: -100, oy: -100 }, 0, 0, 640, 480);
    expect(plan).toMatchObject({ sx: 0, sy: 0, sw: 640, sh: 480 });
  });

  it('长边超上限只缩不放', () => {
    const plan = planShot(null, 0, 0, 4000, 2000, 1000);
    expect(plan).toMatchObject({ sw: 4000, sh: 2000, dw: 1000, dh: 500 });
  });

  it('小图不放大（截图放大只会更糊）', () => {
    const plan = planShot(null, 0, 0, 100, 50);
    expect(plan).toMatchObject({ dw: 100, dh: 50 });
  });

  it('缺缩略图尺寸 → null', () => {
    expect(planShot(null, 0, 0, 0, 480)).toBeNull();
    expect(planShot(null, 0, 0, 640, 0)).toBeNull();
  });

  it('默认长边上限就是 SHOT_MAX_SIDE', () => {
    const plan = planShot(null, 0, 0, SHOT_MAX_SIDE * 2, SHOT_MAX_SIDE);
    expect(plan!.dw).toBe(SHOT_MAX_SIDE);
  });
});
