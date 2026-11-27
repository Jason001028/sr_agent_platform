import { fetchAssetTile, fetchAssetTileInfo } from './imageOpen';
import type { AssetTile, AssetTileInfo, PreviewAsset } from './imageOpen';
import type { DisplayStretch } from './histogramStretch';

export const TILE_OVERVIEW_EDGE = 3200;

export function bootstrapTileLevel(info: AssetTileInfo) {
  const whole = Math.ceil(Math.log2(Math.max(info.width, info.height) / info.tileSize));
  // The first frame only needs one sample per two TIFF blocks on each axis.
  // The viewport scheduler supplies the normal display LOD once it is visible.
  const sparse = info.layout === 'tiled' && !info.overviews.length
    ? Math.ceil(Math.log2(Math.max(...info.block_size))) + 1 : 0;
  return Math.min(info.maxLevel, Math.max(0, whole, sparse));
}

export interface TileSource {
  asset: PreviewAsset;
  info: AssetTileInfo;
  key: string;
  display?: string;
  sampling?: 'nearest' | 'area';
  baseKey?: string;
}

export function tileDisplayKey(stretch?: DisplayStretch) {
  if (!stretch?.custom) return stretch?.mode ?? 'equal';
  const values = [stretch.low, stretch.high, ...(stretch.curve ?? []).flatMap(p => [p.x, p.y])];
  const encoded = values.map(v => (v || 0).toExponential(11));
  return Number(encoded[1]) > Number(encoded[0]) ? `window:${encoded.join(':')}` : 'linear';
}

export function withTileDisplay(source: TileSource, display: string): TileSource {
  if (!source.info.displayProfiles || (display === 'equal' && source.sampling !== 'area')) return source;
  const baseKey = source.baseKey ?? source.key;
  return { ...source, baseKey, display, key: JSON.stringify([baseKey, display, source.sampling ?? 'nearest']) };
}

export async function openTileSource(asset: PreviewAsset, signal?: AbortSignal): Promise<TileSource | null> {
  try {
    const info = await fetchAssetTileInfo(asset, signal);
    if (info.sourceVersion !== asset.sourceRev || info.width !== asset.W || info.height !== asset.H
      || info.band_count !== 1 || info.tileSize !== 512 || !info.rasterVersion) return null;
    return { asset, info, key: JSON.stringify([
      asset.source?.identity ?? asset.id, info.rasterVersion, info.band, info.stretchVersion,
    ]) };
  } catch (error) {
    if (signal?.aborted) throw error;
    return null; // Older API, unavailable GDAL or unsupported raster can still display JPEG.
  }
}

export function overviewDiv(width: number, height: number, requested: number) {
  const required = Math.max(width, height) / TILE_OVERVIEW_EDGE;
  return Math.min(32, Math.max(requested, 2 ** Math.ceil(Math.log2(required))));
}

export async function loadTile(source: TileSource, level: number, x: number, y: number, signal: AbortSignal) {
  const { tile, blob } = await fetchAssetTile(source.asset, level, x, y, signal, source.info.band,
    source.info.displayProfiles ? (source.display ?? 'equal') : undefined,
    source.sampling, source.info.displayProfiles ? source.info.stretchVersion : undefined);
  const { info } = source;
  const step = 2 ** level, span = info.tileSize * step;
  const w = Math.min(span, info.width - x * span), h = Math.min(span, info.height - y * span);
  if (tile.rasterVersion !== info.rasterVersion || tile.sourceVersion !== info.sourceVersion
    || tile.stretchVersion !== info.stretchVersion || tile.band !== info.band
    || (info.displayProfiles && (tile.displayKey !== (source.display ?? 'equal')
      || tile.requestedSampling !== (source.sampling ?? 'nearest')
      || tile.resampling !== (source.sampling === 'area' && !info.categorical && source.display !== 'binary'
        && (level === 1 || level === 2) ? 'area' : 'nearest')))
    || tile.level !== level || tile.tileX !== x || tile.tileY !== y
    || tile.x !== x * span || tile.y !== y * span || tile.sourceWidth !== w || tile.sourceHeight !== h
    || tile.width !== Math.ceil(w / step) || tile.height !== Math.ceil(h / step)) {
    throw new Error('Tile source or geometry changed');
  }
  signal.throwIfAborted();
  const bitmap = await createImageBitmap(blob);
  if (signal.aborted || bitmap.width !== tile.width || bitmap.height !== tile.height) {
    bitmap.close();
    signal.throwIfAborted();
    throw new Error('Tile bitmap dimensions changed');
  }
  return { tile, bitmap };
}

export interface DecodedTile {
  tile: AssetTile;
  bitmap: ImageBitmap;
}
