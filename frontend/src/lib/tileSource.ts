import { fetchAssetTile, fetchAssetTileInfo } from './imageOpen';
import type { AssetTile, AssetTileInfo, PreviewAsset } from './imageOpen';

export const TILE_OVERVIEW_EDGE = 3200;

export interface TileSource {
  asset: PreviewAsset;
  info: AssetTileInfo;
  key: string;
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
  const { tile, blob } = await fetchAssetTile(source.asset, level, x, y, signal, source.info.band);
  const { info } = source;
  const step = 2 ** level, span = info.tileSize * step;
  const w = Math.min(span, info.width - x * span), h = Math.min(span, info.height - y * span);
  if (tile.rasterVersion !== info.rasterVersion || tile.sourceVersion !== info.sourceVersion
    || tile.stretchVersion !== info.stretchVersion || tile.band !== info.band
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
