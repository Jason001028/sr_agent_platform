import { rasterTransform, tileLevel } from './viewMath';
import type { PaneRect, ViewState } from './viewMath';
import { loadTile } from './tileSource';
import type { DecodedTile, TileSource } from './tileSource';
import { ImageOpenError } from './imageOpen';

export interface TilePane {
  source: TileSource;
  rect: PaneRect;
  view: ViewState;
  tw: number;
  th: number;
  hasOverview?: boolean;
}

interface TileRequest {
  key: string;
  source: TileSource;
  level: number;
  x: number;
  y: number;
  priority: number;
  fade: number;
}

export interface CachedTile extends DecodedTile {
  key: string;
  sourceKey: string;
  ready: number;
  fade: number;
}

export function tileRequests(pane: TilePane, dpr: number, previous?: number, direction = { x: 0, y: 0 },
  maxVisible = 96) {
  const { source, rect, view } = pane;
  const { width, height, maxLevel, tileSize } = source.info;
  const scale = rasterTransform(view, pane.tw, pane.th, width, height, rect.w, rect.h, dpr);
  let level = tileLevel(Math.max(scale.x, scale.y) * dpr, maxLevel, previous);
  const left = -scale.ox / scale.x, top = -scale.oy / scale.y;
  const right = left + rect.w / scale.x, bottom = top + rect.h / scale.y;
  if (right <= 0 || bottom <= 0 || left >= width || top >= height || !rect.w || !rect.h) {
    return { level, requests: [] };
  }
  const bounds = (span: number) => ({
    x0: Math.max(0, Math.floor(left / span)), y0: Math.max(0, Math.floor(top / span)),
    x1: Math.ceil(Math.min(right, width) / span) - 1, y1: Math.ceil(Math.min(bottom, height) / span) - 1,
  });
  let span = tileSize * 2 ** level;
  let { x0, x1, y0, y1 } = bounds(span);
  while ((x1 - x0 + 1) * (y1 - y0 + 1) > maxVisible && level < maxLevel) {
    level++;
    span *= 2;
    ({ x0, x1, y0, y1 } = bounds(span));
  }
  const cx = (Math.max(left, 0) + Math.min(right, width)) / (2 * span);
  const cy = (Math.max(top, 0) + Math.min(bottom, height)) / (2 * span);
  const requests: TileRequest[] = [];
  const fade = previous !== undefined && previous !== level ? 200 : direction.x || direction.y ? 120 : 160;
  for (let y = Math.max(0, y0 - 1); y <= Math.min(Math.ceil(height / span) - 1, y1 + 1); y++) {
    for (let x = Math.max(0, x0 - 1); x <= Math.min(Math.ceil(width / span) - 1, x1 + 1); x++) {
      const visible = x >= x0 && x <= x1 && y >= y0 && y <= y1;
      const ahead = (direction.x < 0 && x < x0) || (direction.x > 0 && x > x1)
        || (direction.y < 0 && y < y0) || (direction.y > 0 && y > y1);
      const distance = Math.hypot(x + .5 - cx, y + .5 - cy);
      const priority = (visible ? 0 : ahead ? 2 : 4) + distance / (1 + distance);
      requests.push({ key: `${source.key}/${level}/${x}/${y}`, source, level, x, y, priority, fade });
    }
  }
  return { level, requests };
}

export class TileScheduler {
  readonly cache = new Map<string, CachedTile>();
  bytes = 0;
  peakBytes = 0;
  private demand = new Map<string, TileRequest>();
  private pending = new Map<string, { controller: AbortController; prefetch: boolean }>();
  private failed = new Set<string>();
  private sources = new Map<string, TileSource>();
  private protected = new Set<string>();
  private disposed = false;
  private retryTimers = new Map<string, ReturnType<typeof setTimeout>>();
  private retries = new Map<string, number>();

  constructor(private frame: () => void, private budget = 128 * 1024 ** 2,
    private concurrency = 6, private read = loadTile) {}

  update(requests: TileRequest[], sources: TileSource[], protectedKeys: Set<string>) {
    const next = new Map<string, TileRequest>();
    for (const request of requests) {
      if ((next.get(request.key)?.priority ?? Infinity) > request.priority) next.set(request.key, request);
    }
    if (this.sources.size !== sources.length || sources.some(source => this.sources.get(source.asset.id)?.key !== source.key)) {
      const preserve = new Set(sources.map(source => source.key));
      this.sources = new Map(sources.map(source => [source.asset.id, source]));
      for (const [key, value] of this.cache) if (!preserve.has(value.sourceKey)) this.release(key, value);
    }
    for (const [key, request] of next) {
      if (request.priority < 1 && (this.demand.get(key)?.priority ?? 0) >= 1 && !this.retryTimers.has(key)) {
        this.failed.delete(key);
        this.retries.delete(key);
      }
    }
    this.demand = next;
    this.protected = protectedKeys;
    const visiblePending = requests.some(req => req.priority < 1 && !this.cache.has(req.key)
      && !this.failed.has(req.key) && !this.pending.has(req.key));
    for (const [key, { controller, prefetch }] of this.pending) {
      const request = next.get(key);
      if (!request || (visiblePending && request.priority >= 1)
        || (prefetch && request.level === 0 && request.priority < 1)) controller.abort();
    }
    for (const key of this.failed) if (!next.has(key)) this.failed.delete(key);
    for (const [key, timer] of this.retryTimers) {
      if (!next.has(key)) {
        clearTimeout(timer);
        this.retryTimers.delete(key);
      }
    }
    for (const key of this.retries.keys()) if (!next.has(key)) this.retries.delete(key);
    this.trim();
    this.pump();
  }

  touch(entry: CachedTile) {
    this.cache.delete(entry.key);
    this.cache.set(entry.key, entry);
  }

  private release(key: string, entry: CachedTile) {
    this.cache.delete(key);
    this.bytes -= entry.bitmap.width * entry.bitmap.height * 4;
    entry.bitmap.close();
  }

  private trim() {
    for (const [key, entry] of this.cache) {
      if (this.bytes <= this.budget) break;
      if (!this.protected.has(key) && (this.demand.get(key)?.priority ?? Infinity) >= 1) this.release(key, entry);
    }
  }

  private pump() {
    if (this.disposed || this.pending.size >= this.concurrency) return;
    const work = [...this.demand.values()].filter(request =>
      !this.cache.has(request.key) && !this.pending.has(request.key) && !this.failed.has(request.key))
      .sort((a, b) => a.priority - b.priority);
    for (const request of work) {
      if (this.pending.size >= this.concurrency) break;
      const { key, source, level, x, y } = request;
      const prefetch = request.priority >= 1;
      if (prefetch && this.bytes + 512 * 512 * 4 > this.budget) break;
      const controller = new AbortController();
      this.pending.set(key, { controller, prefetch });
      void this.read(source, level, x, y, controller.signal, prefetch).then(decoded => {
        const current = this.demand.get(key);
        if (controller.signal.aborted || !current || this.sources.get(source.asset.id)?.key !== source.key) {
          decoded.bitmap.close();
          return;
        }
        this.bytes += decoded.bitmap.width * decoded.bitmap.height * 4;
        this.retries.delete(key);
        this.cache.set(key, { ...decoded, key, sourceKey: source.key, ready: performance.now(),
          fade: current.priority < 1 ? current.fade : 0 });
        this.trim();
        this.peakBytes = Math.max(this.peakBytes, this.bytes);
        this.frame();
      }).catch(error => {
        if (controller.signal.aborted) return;
        this.failed.add(key);
        if ((this.demand.get(key)?.priority ?? Infinity) < 1) this.retryVisible(key, error);
      }).finally(() => {
        this.pending.delete(key);
        this.pump();
      });
    }
  }

  private retryVisible(key: string, error: unknown) {
    const attempts = this.retries.get(key) ?? 0;
    const overloaded = error instanceof ImageOpenError && error.status === 503;
    if (attempts >= (overloaded ? 4 : 1)) return;
    const delay = overloaded ? Math.max(error.retryAfterMs ?? 0, 1000 * 2 ** attempts) : 700;
    if (delay > 2_147_483_647) return;
    this.retries.set(key, attempts + 1);
    this.retryTimers.set(key, setTimeout(() => {
      this.retryTimers.delete(key);
      if ((this.demand.get(key)?.priority ?? Infinity) < 1) {
        this.failed.delete(key);
        this.pump();
      }
    }, delay));
  }

  close() {
    this.disposed = true;
    for (const timer of this.retryTimers.values()) clearTimeout(timer);
    this.retryTimers.clear();
    for (const { controller } of this.pending.values()) controller.abort();
    for (const [key, entry] of this.cache) this.release(key, entry);
    this.demand.clear();
    this.sources.clear();
  }
}
