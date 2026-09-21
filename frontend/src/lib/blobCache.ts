/**
 * blobCache.ts — 按字节封顶的 LRU blob 缓存（2026-09-20 新增）
 * ------------------------------------------------------------------
 * 给预览 JPEG 用：同一张图来回切（对比模式）不该每次都走一遍网络；磁盘上那份
 * 服务端已经烤好，前端再取就是纯重复。
 *
 * **为什么缓存 blob 而不是解码后的位图**：一条 rec 的像素本来就常驻
 * （`thumb` 画布 4 B/px + `src` Float32Array 4 B/px = 8 B/px，尺寸 = 服务端 jpg 的
 * 原生尺寸、不封顶），÷2 档一张就是几百 MB；而「已经在点选清单里的图」像素本来就在
 * rec 里，位图缓存只对「关掉再打开」有用，代价却是再翻一倍。blob 是压缩态
 * （几 MB～几十 MB），这才是值得留下的那一层。
 *
 * **按字节封顶，不按条数**：条数在大图上完全不代表内存 —— 十条 2 MB 的和十条
 * 200 MB 的一样叫「十条」。
 *
 * 纯函数 + 无 DOM：`Map` 的插入序当 LRU 队列，`Blob.size` 当权重，可单测。
 */

export interface BlobCacheStats {
  count: number;
  bytes: number;
  maxBytes: number;
}

export interface BlobCache {
  get(key: string): Blob | undefined;
  set(key: string, blob: Blob): void;
  /** 清空（设置浮层里那颗「清空」按钮）。 */
  clear(): void;
  stats(): BlobCacheStats;
}

/** 造一个按字节封顶的 LRU 缓存。
 *
 * `maxBytes <= 0` 时退化成「什么都不缓存」：门还开着，`set` 直接丢。
 */
export function createBlobCache(maxBytes: number): BlobCache {
  /** 插入序 = 从旧到新。`get` 命中会把它挪到末尾（刷新新鲜度）。 */
  const items = new Map<string, Blob>();
  let bytes = 0;

  function dropOldest(): void {
    // Map 的 keys() 是插入序，第一个就是最旧的
    const first = items.keys().next();
    if (first.done) return;
    const b = items.get(first.value);
    items.delete(first.value);
    if (b) bytes -= b.size;
  }

  return {
    get(key) {
      const b = items.get(key);
      if (b === undefined) return undefined;
      // 命中即刷新：先删再塞，它就排到末尾去了
      items.delete(key);
      items.set(key, b);
      return b;
    },

    set(key, blob) {
      if (!(maxBytes > 0)) return;
      // 单条就超过上限的不进缓存：放进来会立刻把自己挤出去（连带上一条也被挤掉），
      // 白折腾一遍还污染别人的位置。直接说清「这条太大，不留」。
      if (blob.size > maxBytes) return;
      const old = items.get(key);
      if (old !== undefined) {
        items.delete(key);
        bytes -= old.size;
      }
      items.set(key, blob);
      bytes += blob.size;
      while (bytes > maxBytes && items.size > 0) dropOldest();
    },

    clear() {
      items.clear();
      bytes = 0;
    },

    stats() {
      return { count: items.size, bytes, maxBytes };
    },
  };
}
