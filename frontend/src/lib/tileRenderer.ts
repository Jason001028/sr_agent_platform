import type { CachedTile, TilePane } from './tileScheduler';
import { TileScheduler, tileRequests } from './tileScheduler';
import type { TileSource } from './tileSource';
import { bootstrapTileLevel } from './tileSource';
import { easeOutCubic, rasterTransform } from './viewMath';

export function tileAxis(start: number, span: number, pixels: number, step: number) {
  const full = Math.floor(span / step);
  const edge = start + full * step;
  if (full === pixels) return [{ start, end: start + span, from: 0, count: pixels }];
  const parts = full ? [{ start, end: edge, from: 0, count: full }] : [];
  parts.push({ start: edge, end: start + span, from: full, count: 1 });
  return parts;
}

interface Camera { key: string; level: number; ox: number; oy: number; scale: number; levelChanged: number; time: number }
type TileCoverage = 'pending' | 'fading' | 'opaque';

export class TileRenderer {
  readonly scheduler: TileScheduler;
  private cameras = new Map<string, Camera>();
  private layers = new Map<string, { pane: TilePane; tiles: CachedTile[]; level: number; ready: number;
    total: number; opaque: boolean; coverage: TileCoverage; drawn: number; transform: ReturnType<typeof rasterTransform> }>();
  private recent = new Map<string, { at: number; x: number; y: number; speed: number }>();
  private geometry = new WeakMap<CachedTile, { x: ReturnType<typeof tileAxis>; y: ReturnType<typeof tileAxis> }>();

  constructor(frame: () => void) {
    this.scheduler = new TileScheduler(frame);
  }

  update(panes: Map<string, TilePane>, sources: TileSource[], now: number, dpr: number, reducedMotion: boolean) {
    const pins = new Set<string>();
    const wanted: ReturnType<typeof tileRequests>['requests'] = [];
    let cached: CachedTile[] | undefined;
    this.layers.clear();
    for (const [id, pane] of panes) {
      const prior = this.cameras.get(id);
      const cameraKey = pane.source.baseKey ?? pane.source.key;
      const old = prior?.key === cameraKey ? prior : undefined;
      const { ox, oy, scale } = pane.view;
      const dx = old && old.scale === scale ? old.ox - ox : 0;
      const dy = old && old.scale === scale ? old.oy - oy : 0;
      const movement = Math.hypot(dx, dy);
      if (movement > 0 && old) this.recent.set(id, {
        at: now, x: dx, y: dy, speed: movement * 1000 / Math.max(8, now - old.time),
      });
      const last = this.recent.get(id);
      const direction = old?.scale === scale && last && now - last.at < 120 ? last : undefined;
      const { level, requests } = tileRequests(pane, dpr, old?.level, direction, Math.floor(96 / panes.size));
      const levelChanged = old && old.level !== level ? now : old?.levelChanged ?? -Infinity;
      this.cameras.set(id, { key: cameraKey, level, ox, oy, scale, levelChanged, time: now });
      const s = rasterTransform(pane.view, pane.tw, pane.th, pane.source.info.width, pane.source.info.height,
        pane.rect.w, pane.rect.h, dpr);
      const span = pane.source.info.tileSize * 2 ** level;
      const intersects = (start: number, end: number, offset: number, size: number) =>
        Math.round(start * dpr) < Math.round(end * dpr)
        && Math.round(end * dpr) > offset * dpr && Math.round(start * dpr) < (offset + size) * dpr;
      const { rect, source: { info } } = pane;
      const visible = requests.filter(req => req.priority < 1
        && intersects(rect.x + s.ox + req.x * span * s.x,
          rect.x + s.ox + Math.min((req.x + 1) * span, info.width) * s.x, rect.x, rect.w)
        && intersects(rect.y + s.oy + req.y * span * s.y,
          rect.y + s.oy + Math.min((req.y + 1) * span, info.height) * s.y, rect.y, rect.h));
      const target = visible.flatMap(req => {
        const tile = this.scheduler.cache.get(req.key);
        return tile ? [tile] : [];
      });
      const opaque = (entry: CachedTile) => reducedMotion || now - entry.ready >= entry.fade;
      const complete = target.length === visible.length && target.every(opaque);
      const fallback = complete ? [] : (cached ??= [...this.scheduler.cache.values()]).filter(entry => {
        const t = entry.tile;
        return entry.sourceKey === pane.source.key && t.level !== level
          && s.ox + (t.x + t.sourceWidth) * s.x > 0 && s.ox + t.x * s.x < pane.rect.w
          && s.oy + (t.y + t.sourceHeight) * s.y > 0 && s.oy + t.y * s.y < pane.rect.h;
      }).sort((a, b) => b.tile.level - a.tile.level).slice(-Math.floor(24 / panes.size));
      const wholeOverview = fallback.find(({ tile }) => tile.x === 0 && tile.y === 0
        && tile.sourceWidth === info.width && tile.sourceHeight === info.height);
      const targetReady = target.length > 0 && target.length === visible.length;
      const coverage = (targetReady && complete) || (wholeOverview && opaque(wholeOverview)) ? 'opaque'
        : targetReady || wholeOverview ? 'fading' : 'pending';
      const tiles = [...fallback, ...target];
      for (const tile of tiles) {
        pins.add(tile.key);
        this.scheduler.touch(tile);
      }
      for (const request of requests) {
        if (pane.source.display === 'binary' || reducedMotion || (direction && direction.speed >= 1000)) request.fade = 0;
        else if (now - levelChanged < 220) request.fade = 200;
        wanted.push(request);
      }
      if (info.displayProfiles && !pane.hasOverview && !complete && visible.length) {
        const whole = bootstrapTileLevel(info);
        const key = `${pane.source.key}/${whole}/0/0`;
        if (!requests.some(request => request.key === key)) {
          wanted.push({ key, source:pane.source, level:whole, x:0, y:0, priority:0.95, fade:0 });
        }
      }
      this.layers.set(id, { pane, tiles, level, ready: target.length, total: visible.length,
        opaque: complete && targetReady, coverage, drawn: 0, transform: s });
    }
    for (const id of this.cameras.keys()) {
      if (!panes.has(id)) {
        this.cameras.delete(id);
        this.recent.delete(id);
      }
    }
    this.scheduler.update(wanted, sources, pins);
  }

  status(id: string) {
    const layer = this.layers.get(id);
    return layer && { level: layer.level, ready: layer.ready, total: layer.total };
  }

  covers(id: string) {
    return this.layers.get(id)?.opaque === true;
  }

  coverage(id: string): TileCoverage {
    return this.layers.get(id)?.coverage ?? 'pending';
  }

  presentation(id: string) {
    const layer = this.layers.get(id);
    if (!layer) return;
    return {
      targetLOD: layer.level,
      readyLOD: layer.total > 0 && layer.drawn === layer.total ? layer.level : null,
      visible: layer.total,
      drawn: layer.drawn,
      scale: { x: layer.transform.x, y: layer.transform.y },
    };
  }

  draw(ctx: CanvasRenderingContext2D, id: string, now: number, dpr: number, immediate: boolean) {
    const layer = this.layers.get(id);
    if (!layer) return false;
    const { pane: { rect }, tiles, transform: s } = layer;
    const snap = (v: number) => Math.round(v * dpr) / dpr;
    ctx.save();
    ctx.beginPath();
    ctx.rect(rect.x, rect.y, rect.w, rect.h);
    ctx.clip();
    let active = false;
    layer.drawn = 0;
    for (const entry of tiles) {
      const t = entry.tile;
      const step = 2 ** t.level;
      ctx.globalAlpha = immediate || !entry.fade ? 1 : easeOutCubic((now - entry.ready) / entry.fade);
      if (ctx.globalAlpha < 1) active = true;
      if (ctx.globalAlpha === 0) continue;
      ctx.imageSmoothingEnabled = layer.pane.source.display !== 'binary' && Math.max(s.x, s.y) * step * dpr < 1;
      let axes = this.geometry.get(entry);
      if (!axes) {
        axes = { x: tileAxis(t.x, t.sourceWidth, t.width, step), y: tileAxis(t.y, t.sourceHeight, t.height, step) };
        this.geometry.set(entry, axes);
      }
      let painted = false;
      for (const x of axes.x) {
        for (const y of axes.y) {
          const sx = snap(rect.x + s.ox + x.start * s.x);
          const sy = snap(rect.y + s.oy + y.start * s.y);
          const w = snap(rect.x + s.ox + x.end * s.x) - sx;
          const h = snap(rect.y + s.oy + y.end * s.y) - sy;
          if (w > 0 && h > 0) {
            ctx.drawImage(entry.bitmap, x.from, y.from, x.count, y.count, sx, sy, w, h);
            painted ||= sx < rect.x + rect.w && sx + w > rect.x
              && sy < rect.y + rect.h && sy + h > rect.y;
          }
        }
      }
      // P2 encodes opaque grayscale PNGs, including black NoData samples.
      if (painted && ctx.globalAlpha === 1 && t.level === layer.level) layer.drawn++;
    }
    ctx.restore();
    return active;
  }
}
