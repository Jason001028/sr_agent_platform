/**
 * imageFiles.test.ts — 影像文件分类（§4.6 放宽文件过滤）
 * ------------------------------------------------------------------
 * 覆盖：扩展名 / MIME 各自命中、大小写、两种 TIF 扩展、不支持的格式被丢弃、
 * 混合批次的顺序保持（TIF 与 JPG 各自成堆，互不串味）。
 */
import { describe, it, expect } from 'vitest';
import { imageKindOf, classifyImages } from '../imageFiles.js';

const f = (name: string, type = ''): { name: string; type: string } => ({ name, type });

describe('imageKindOf', () => {
  it('TIF/TIFF（含大写）→ tif', () => {
    expect(imageKindOf(f('a.tif'))).toBe('tif');
    expect(imageKindOf(f('a.tiff'))).toBe('tif');
    expect(imageKindOf(f('GF07A03.TIF'))).toBe('tif');
    expect(imageKindOf(f('GF07A03.TIFF'))).toBe('tif');
  });

  it('JPG/JPEG（含大写）→ jpg', () => {
    expect(imageKindOf(f('a.jpg'))).toBe('jpg');
    expect(imageKindOf(f('a.jpeg'))).toBe('jpg');
    expect(imageKindOf(f('预览.JPG'))).toBe('jpg');
  });

  it('扩展名缺失时靠 MIME 命中', () => {
    expect(imageKindOf(f('无扩展名', 'image/tiff'))).toBe('tif');
    expect(imageKindOf(f('无扩展名', 'image/jpeg'))).toBe('jpg');
  });

  it('扩展名与 MIME 不一致时以扩展名为准（TIF 优先判定）', () => {
    // 浏览器偶尔给 image/jpeg 之外的空 MIME；这里只保证不会把 .tif 错判成 jpg
    expect(imageKindOf(f('a.tif', 'image/jpeg'))).toBe('tif');
  });

  it('其它格式 → null', () => {
    expect(imageKindOf(f('a.png', 'image/png'))).toBeNull();
    expect(imageKindOf(f('a.txt'))).toBeNull();
    expect(imageKindOf(f(''))).toBeNull();
    // 不能把 .jpgis 之类的前缀混淆当成合法扩展
    expect(imageKindOf(f('a.jpgx'))).toBeNull();
    expect(imageKindOf(f('a.tifx'))).toBeNull();
  });
});

describe('classifyImages', () => {
  it('混合批次分两堆，各自保持输入顺序', () => {
    const { tifs, imgs } = classifyImages([
      f('a.tif'), f('b.jpg'), f('c.tiff'), f('d.jpeg'), f('e.png'),
    ]);
    expect(tifs.map((x) => x.name)).toEqual(['a.tif', 'c.tiff']);
    expect(imgs.map((x) => x.name)).toEqual(['b.jpg', 'd.jpeg']);
  });

  it('只有 TIF / 只有 JPG', () => {
    expect(classifyImages([f('a.tif')])).toEqual({ tifs: [f('a.tif')], imgs: [] });
    expect(classifyImages([f('a.jpg')])).toEqual({ tifs: [], imgs: [f('a.jpg')] });
  });

  it('全不支持 → 两堆皆空（调用方据此报错）', () => {
    const got = classifyImages([f('a.png'), f('b.txt')]);
    expect(got.tifs).toEqual([]);
    expect(got.imgs).toEqual([]);
  });

  it('空输入不报错', () => {
    expect(classifyImages([])).toEqual({ tifs: [], imgs: [] });
  });
});
