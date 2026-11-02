/**
 * batchOverlay.test.ts — 批量检测叠加层前端契约（PR D / P1 2）
 * ------------------------------------------------------------------
 * 四类断言：
 * 1. URL 入口：只认 `batch_goal` + `batch_item` 成对出现，ID 必须转义。
 * 2. 文档校验：契约版本 / Goal-Item 身份 / 源身份 / provenance / overlay 结构，
 *    任一不合格都返回**稳定原因码**（组件据此显示失败，不靠异常猜）。
 * 3. 坐标：同一套 `origToThumb → thumbToScreen` 数学，缩放/平移/分屏各用各的
 *    view；两格位置互不影响。候选 bbox 闭合四角、多边形逐点投影。
 * 4. 加载失败：HTTP 错误、网络拒绝、坏 JSON、串号、取消，全部收敛为原因码。
 */
import { describe, it, expect } from 'vitest';
import {
  batchOverlayUrls, candidateScreenPoints, loadBatchOverlay,
  overlayFailureText, overlayImageUrl, overlayMatchesImage, overlayPaneRect,
  parseBatchOverlayParams, provenanceLabel, validateBatchOverlayDoc,
} from '../batchOverlay';
import type { BatchOverlayDoc } from '../batchOverlay';
import type { ImageRef } from '../spatial';

const params = { goalId: 'goal-1', itemId: 'item/1' };
const image: ImageRef = {
  image_key: 'scene-v1:aaaa:bbbb', source_width: 12000, source_height: 8000,
};

function doc(overrides: Partial<BatchOverlayDoc> = {}): BatchOverlayDoc {
  return {
    contract_version: 'overlay.v1',
    goal_id: 'goal-1',
    item_id: 'item/1',
    item_version: 7,
    source: {
      image_key: 'scene-v1:aaaa:bbbb', revision: 'rev-1',
      width: 12000, height: 8000,
    },
    provenance: 'real',
    detector_name: 'flatline-scan',
    detector_version: '1.0.0',
    candidates: [],
    overlay: {
      url: '/api/goals/goal-1/items/item%2F1/overlay.png',
      width: 512, height: 342, sha256: 'abc',
    },
    ...overrides,
  };
}

function response(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

describe('URL 入口与路径', () => {
  it('只认成对出现的 batch_goal / batch_item', () => {
    expect(parseBatchOverlayParams('?batch_goal=g&batch_item=i')).toEqual({
      goalId: 'g', itemId: 'i',
    });
    expect(parseBatchOverlayParams('?batch_goal=g')).toBeNull();
    expect(parseBatchOverlayParams('?batch_item=i')).toBeNull();
    expect(parseBatchOverlayParams('?batch_goal=%20&batch_item=i')).toBeNull();
    expect(parseBatchOverlayParams('')).toBeNull();
  });

  it('ID 一律 encodeURIComponent，不把客户端字符串拼成路径', () => {
    const urls = batchOverlayUrls(params, '');
    expect(urls.meta).toBe('/api/goals/goal-1/items/item%2F1/overlay');
    expect(urls.png).toBe('/api/goals/goal-1/items/item%2F1/overlay.png');
  });

  it('跨域 apiBase 与刷新缓存键', () => {
    const urls = batchOverlayUrls(params, 'http://host:9000', 7);
    expect(urls.meta).toBe('http://host:9000/api/goals/goal-1/items/item%2F1/overlay?v=7');
    expect(urls.png).toContain('/overlay.png?v=7');
    expect(overlayImageUrl(doc(), 'http://host:9000')).toBe(
      'http://host:9000/api/goals/goal-1/items/item%2F1/overlay.png');
    expect(overlayImageUrl(doc(), '', 'abc')).toContain('?v=abc');
  });
});

describe('文档校验 —— 稳定原因码', () => {
  it('完整文档通过', () => {
    expect(validateBatchOverlayDoc(doc(), params).ok).toBe(true);
  });

  it.each([
    ['契约版本', doc({ contract_version: 'overlay.v2' } as Partial<BatchOverlayDoc>), 'unsupported_contract'],
    ['串到别的 Item', doc({ item_id: 'item-2' }), 'identity_mismatch'],
    ['Goal 不一致', doc({ goal_id: 'goal-2' }), 'identity_mismatch'],
    ['源身份缺失', doc({ source: { image_key: '', revision: 'r', width: 1, height: 1 } }), 'invalid_source'],
    ['provenance 非法', doc({ provenance: 'fake' as never }), 'invalid_provenance'],
    ['候选不是数组', doc({ candidates: null as never }), 'invalid_candidates'],
    ['overlay 路径不是同源相对路径', doc({
      overlay: { url: 'javascript:alert(1)', width: 512, height: 342 },
    }), 'invalid_overlay'],
    ['overlay 尺寸非正', doc({
      overlay: { url: '/x.png', width: 0, height: 342 },
    }), 'invalid_overlay'],
  ])('拒绝：%s', (_label, value, reason) => {
    const checked = validateBatchOverlayDoc(value, params);
    expect(checked.ok).toBe(false);
    if (!checked.ok) expect(checked.reason).toBe(reason);
  });
});

describe('身份匹配只认 image_key', () => {
  it('同键匹配；尺寸不参与（查看器显示的是预览 JPG）', () => {
    expect(overlayMatchesImage(doc(), image)).toBe(true);
    expect(overlayMatchesImage(doc(), { image_key: 'scene-v1:aaaa:bbbb' })).toBe(true);
    const smallerPreview: ImageRef = { ...image, source_width: 10, source_height: 10 };
    expect(overlayMatchesImage(doc(), smallerPreview)).toBe(true);
  });

  it('换图 / 空身份都不画', () => {
    expect(overlayMatchesImage(doc(), { ...image, image_key: 'scene-v1:aaaa:cccc' })).toBe(false);
    expect(overlayMatchesImage(doc(), null)).toBe(false);
    expect(overlayMatchesImage(null, image)).toBe(false);
  });
});

describe('坐标：每格用自己的 view', () => {
  // 12000×8000 的 10% 缩略图 1200×800；scale=2 → 屏幕 2400×1600
  const view = { scale: 2, ox: -100, oy: -50 };
  const local = { x: 0, y: 0, w: 500, h: 600 };

  it('整幅影像范围映射成格内矩形（缩放 + 平移）', () => {
    expect(overlayPaneRect(image, 1200, 800, view, local)).toEqual({
      left: -100, top: -50, width: 2400, height: 1600,
    });
    expect(overlayPaneRect(image, 1200, 800, { scale: 4, ox: 20, oy: 30 }, local)).toEqual({
      left: 20, top: 30, width: 4800, height: 3200,
    });
  });

  it('带上格子自身偏移：分屏右格不能画到左格原点', () => {
    const left = { x: 0, y: 0, w: 640, h: 600 };
    const right = { x: 640, y: 12, w: 640, h: 600 };
    const viewLeft = { scale: 1, ox: 20, oy: 30 };
    expect(overlayPaneRect(image, 1200, 800, viewLeft, left))
      .toMatchObject({ left: 20, top: 30 });
    expect(overlayPaneRect(image, 1200, 800, viewLeft, right))
      .toMatchObject({ left: 660, top: 42 });
    const points = candidateScreenPoints(
      { candidate_id: 'c1', kind: 'bbox', bbox: [0, 0, 12000, 8000], points: null },
      image, 1200, 800, viewLeft, right);
    expect(points?.[0]).toEqual([660, 42]);
  });

  it('分屏两格不共享投影：同图不同 view 得到不同矩形', () => {
    const a = overlayPaneRect(image, 1200, 800, { scale: 1, ox: 0, oy: 0 }, local);
    const b = overlayPaneRect(image, 1200, 800, { scale: 3, ox: -200, oy: -60 }, local);
    expect(a).toEqual({ left: 0, top: 0, width: 1200, height: 800 });
    expect(b).toEqual({ left: -200, top: -60, width: 3600, height: 2400 });
    expect(a).not.toEqual(b);
  });

  it('bbox 候选投影成闭合四角，多边形逐点投影', () => {
    const bbox = candidateScreenPoints(
      { candidate_id: 'c1', kind: 'bbox', bbox: [0, 0, 12000, 8000], points: null },
      image, 1200, 800, view, local);
    expect(bbox).toEqual([[-100, -50], [2300, -50], [2300, 1550], [-100, 1550]]);
    const polygon = candidateScreenPoints(
      { candidate_id: 'c2', kind: 'polygon', points: [[0, 0], [6000, 0], [6000, 4000]] },
      image, 1200, 800, view, local);
    expect(polygon).toEqual([[-100, -50], [1100, -50], [1100, 750]]);
    expect(candidateScreenPoints(
      { candidate_id: 'c3', kind: 'bbox', bbox: [1, 2, 3] as never }, image, 1200, 800, view, local,
    )).toBeNull();
  });
});

describe('加载失败收敛为原因码', () => {
  it('成功时返回已校验文档', async () => {
    let seen: string | null = null;
    const result = await loadBatchOverlay(params, {
      apiBase: '',
      fetch: (async (url: string) => {
        seen = url;
        return response(doc());
      }) as unknown as typeof fetch,
    });
    expect(seen).toBe('/api/goals/goal-1/items/item%2F1/overlay');
    expect(result.status).toBe('ready');
  });

  it.each([
    ['HTTP 错误', async () => response({}, 503), 'http_503'],
    ['网络拒绝', async () => { throw new TypeError('failed'); }, 'network'],
    ['坏 JSON', async () => ({ ok: true, status: 200, json: async () => { throw new Error('bad'); } }), 'invalid_json'],
    ['串号文档', async () => response(doc({ item_id: 'other' })), 'identity_mismatch'],
  ])('%s', async (_label, impl, reason) => {
    const result = await loadBatchOverlay(params, {
      apiBase: '', fetch: impl as unknown as typeof fetch,
    });
    expect(result).toEqual({ status: 'error', reason });
  });

  it('取消请求不当作失败重试', async () => {
    const controller = new AbortController();
    controller.abort();
    const result = await loadBatchOverlay(params, {
      apiBase: '',
      signal: controller.signal,
      fetch: (async () => { throw Object.assign(new Error('x'), { name: 'AbortError' }); }) as unknown as typeof fetch,
    });
    expect(result).toEqual({ status: 'error', reason: 'aborted' });
  });
});

describe('展示文案', () => {
  it('provenance 必须说清 Mock 不是真实结果', () => {
    expect(provenanceLabel('mock')).toContain('非真实');
    expect(provenanceLabel('real')).toBe('真实检测');
  });

  it('失败原因有中文说明，未知原因原样显示', () => {
    expect(overlayFailureText('http_404')).toBe('服务端返回 404');
    expect(overlayFailureText('identity_mismatch')).toContain('不属于当前批次项');
    expect(overlayFailureText('weird')).toBe('weird');
  });
});
