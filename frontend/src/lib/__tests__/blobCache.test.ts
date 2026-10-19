/**
 * blobCache.test.ts — 按字节封顶的 LRU blob 缓存（2026-09-20）
 * ------------------------------------------------------------------
 * 这是给预览 jpg 用的那一层的判据：谁先被淘汰、上限怎么算、超大单条怎么办。
 * 三条都直接影响真机表现 —— 淘汰顺序错了等于缓存白做（刚看过的被挤掉），
 * 上限算错了等于内存没兜住。
 *
 * Node 环境有 `Blob`（node 18+ 全局），不需要 DOM。
 */
import { describe, it, expect } from 'vitest';
import { createBlobCache } from '../blobCache.js';

/** 造一个 size 恰好为 n 字节的 blob（内容无所谓，缓存只按 size 记账）。 */
function blobOf(n: number): Blob {
  return new Blob([new Uint8Array(n)]);
}

describe('createBlobCache — 基本出入', () => {
  it('set 后 get 拿得回同一个 blob；未存过的键 undefined', () => {
    const c = createBlobCache(1000);
    const b = blobOf(100);
    c.set('a', b);
    expect(c.get('a')).toBe(b);
    expect(c.get('nope')).toBeUndefined();
  });

  it('重复 set 同一个键：只留新的，字节数不叠加', () => {
    const c = createBlobCache(1000);
    c.set('a', blobOf(300));
    c.set('a', blobOf(100));
    expect(c.get('a')?.size).toBe(100);
    expect(c.stats().count).toBe(1);
    expect(c.stats().bytes).toBe(100);
  });

  it('stats 暴露条数 / 字节 / 上限，且 bytes 与各条 size 之和一致', () => {
    const c = createBlobCache(1000);
    c.set('a', blobOf(120));
    c.set('b', blobOf(80));
    const s = c.stats();
    expect(s.count).toBe(2);
    expect(s.bytes).toBe(200);
    expect(s.maxBytes).toBe(1000);
  });
});

describe('createBlobCache — deletePrefix（场景缓存被清后按场景失效）', () => {
  it('只丢命中的那些键，返回丢掉的条数', () => {
    const c = createBlobCache(10000);
    c.set('sc1|2|jpg', blobOf(100));
    c.set('sc1|4|jpg', blobOf(200));
    c.set('sc1|4|ras:PAN.tif', blobOf(300));
    c.set('sc2|4|jpg', blobOf(400));

    expect(c.deletePrefix('sc1|')).toBe(3);

    expect(c.get('sc1|4|jpg')).toBeUndefined();
    expect(c.get('sc2|4|jpg')?.size).toBe(400);
    expect(c.stats().count).toBe(1);
  });

  it('字节数跟着减 —— 只删条目不减 bytes，stats 会越报越大、LRU 也会提前挤掉别人',
    () => {
      const c = createBlobCache(10000);
      c.set('sc1|4|jpg', blobOf(300));
      c.set('sc2|4|jpg', blobOf(200));
      c.deletePrefix('sc1|');
      expect(c.stats().bytes).toBe(200);
      expect(c.stats().count).toBe(1);
    });

  it('纯字符串前缀匹配：不做 id 识别，也不误伤无关的键', () => {
    const c = createBlobCache(10000);
    c.set('sc1|4|jpg', blobOf(100));
    // 'sc1' 也是前缀（本函数只比字符串）—— 所以调用方必须自己补竖线，
    // 否则 'sc10|4|jpg' 这种也会被一起丢掉。见 forgetScenePreviewBlobs。
    expect(c.deletePrefix('sc1')).toBe(1);
    expect(c.deletePrefix('nope|')).toBe(0);
    expect(c.stats().bytes).toBe(0);
  });

  it('丢掉的键再 set 回来是全新的（旧字节不会被复用）', () => {
    const c = createBlobCache(10000);
    c.set('sc1|4|jpg', blobOf(100));
    c.deletePrefix('sc1|');
    expect(c.get('sc1|4|jpg')).toBeUndefined();
    const fresh = blobOf(150);
    c.set('sc1|4|jpg', fresh);
    expect(c.get('sc1|4|jpg')).toBe(fresh);
    expect(c.stats().bytes).toBe(150);
  });
});

describe('createBlobCache — LRU 顺序', () => {
  it('超上限时先淘汰最旧的那条', () => {
    const c = createBlobCache(300);
    c.set('a', blobOf(100));
    c.set('b', blobOf(100));
    c.set('c', blobOf(100));
    c.set('d', blobOf(100));          // 400 > 300 → 挤掉 a
    expect(c.get('a')).toBeUndefined();
    expect(c.get('b')).toBeDefined();
    expect(c.get('d')).toBeDefined();
    expect(c.stats().bytes).toBe(300);
  });

  it('get 命中即刷新新鲜度：被读过的那条不再是最旧的', () => {
    const c = createBlobCache(300);
    c.set('a', blobOf(100));
    c.set('b', blobOf(100));
    c.set('c', blobOf(100));
    c.get('a');                        // a 变成最新 → 下一个被淘汰的该是 b
    c.set('d', blobOf(100));
    expect(c.get('a')).toBeDefined();
    expect(c.get('b')).toBeUndefined();
    expect(c.stats().bytes).toBe(300);
  });

  it('一条就把上限占满时，后来的会把它挤掉（不是留着旧的不放）', () => {
    const c = createBlobCache(100);
    c.set('a', blobOf(100));
    c.set('b', blobOf(100));
    expect(c.get('a')).toBeUndefined();
    expect(c.get('b')).toBeDefined();
    expect(c.stats().count).toBe(1);
  });
});

describe('createBlobCache — 边界', () => {
  it('单条超过上限的直接不进缓存（放了也会立刻把自己挤出去）', () => {
    const c = createBlobCache(100);
    c.set('big', blobOf(101));
    expect(c.get('big')).toBeUndefined();
    expect(c.stats().count).toBe(0);
    expect(c.stats().bytes).toBe(0);
  });

  it('单条超上限不影响已经在里面的（别为了放它清空别人）', () => {
    const c = createBlobCache(100);
    c.set('a', blobOf(60));
    c.set('big', blobOf(500));
    expect(c.get('a')).toBeDefined();
    expect(c.stats().bytes).toBe(60);
  });

  it('大小恰好等于上限的那条留得住（判据是 > 而不是 >=）', () => {
    const c = createBlobCache(100);
    c.set('a', blobOf(100));
    expect(c.get('a')).toBeDefined();
    expect(c.stats().bytes).toBe(100);
  });

  it('maxBytes <= 0 退化成「什么都不缓存」，但不抛', () => {
    for (const m of [0, -1]) {
      const c = createBlobCache(m);
      c.set('a', blobOf(1));
      expect(c.get('a')).toBeUndefined();
      expect(c.stats().count).toBe(0);
    }
  });

  it('clear 后 stats 归零，之前存的都取不回来', () => {
    const c = createBlobCache(1000);
    c.set('a', blobOf(100));
    c.set('b', blobOf(100));
    c.clear();
    expect(c.stats()).toEqual({ count: 0, bytes: 0, maxBytes: 1000 });
    expect(c.get('a')).toBeUndefined();
    // 清空之后还能接着用
    c.set('c', blobOf(50));
    expect(c.stats().bytes).toBe(50);
  });

  it('两个实例互不干扰（模块级单例之外的隔离性）', () => {
    const c1 = createBlobCache(1000);
    const c2 = createBlobCache(1000);
    c1.set('a', blobOf(10));
    expect(c2.get('a')).toBeUndefined();
    expect(c2.stats().count).toBe(0);
  });
});

/* 内容变化的通知（onChange）。设置浮层那一行「本地预览缓存 N 项」靠它保持实时：
   改缓存的按钮（预取开关）就在那一行上面，只在打开时读一次快照，用户就永远看不到
   自己刚触发的那一次 —— 现象是「预取开着、缓存始终 0 项」。判据是**内容真的变了**
   才响，读命中与空操作不响（否则一次取图两次渲染，白响）。 */
describe('createBlobCache — onChange（内容变化通知）', () => {
  it('set 进一条就响一次', () => {
    let n = 0;
    const c = createBlobCache(1000, () => { n++; });
    c.set('a', blobOf(10));
    expect(n).toBe(1);
  });

  it('get 命中（只挪 LRU 次序）不响', () => {
    let n = 0;
    const c = createBlobCache(1000, () => { n++; });
    c.set('a', blobOf(10));
    c.get('a');
    c.get('没这条');
    expect(n).toBe(1);
  });

  it('同键覆盖也算变了（字节数会变），响', () => {
    let n = 0;
    const c = createBlobCache(1000, () => { n++; });
    c.set('a', blobOf(10));
    c.set('a', blobOf(20));
    expect(n).toBe(2);
  });

  it('被拒的单条（超上限 / 上限为 0）不响：缓存里什么都没变', () => {
    let n = 0;
    const c = createBlobCache(100, () => { n++; });
    c.set('big', blobOf(101));
    expect(n).toBe(0);
    let m = 0;
    const z = createBlobCache(0, () => { m++; });
    z.set('a', blobOf(1));
    expect(m).toBe(0);
  });

  it('LRU 淘汰连带响一次（进一条挤掉一条也是内容变化）', () => {
    let n = 0;
    const c = createBlobCache(100, () => { n++; });
    c.set('a', blobOf(60));
    c.set('b', blobOf(60));      // 挤掉 a
    expect(n).toBe(2);
    expect(c.stats().count).toBe(1);
  });

  it('clear：真有东西才响；空缓存上 clear 不响', () => {
    let n = 0;
    const c = createBlobCache(1000, () => { n++; });
    c.clear();
    expect(n).toBe(0);
    c.set('a', blobOf(10));
    c.clear();
    expect(n).toBe(2);
  });

  it('deletePrefix：删掉了才响，一条没删不响', () => {
    let n = 0;
    const c = createBlobCache(1000, () => { n++; });
    c.set('~a|2|jpg', blobOf(10));
    c.set('~b|2|jpg', blobOf(10));
    expect(c.deletePrefix('~zzz|')).toBe(0);
    expect(n).toBe(2);
    expect(c.deletePrefix('~a|')).toBe(1);
    expect(n).toBe(3);
  });

  it('不传 onChange 照常工作（回调是可选的）', () => {
    const c = createBlobCache(1000);
    c.set('a', blobOf(10));
    c.clear();
    expect(c.stats().count).toBe(0);
  });
});
