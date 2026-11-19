// @vitest-environment jsdom
import { expect, it } from 'vitest';
import { sceneRefFromPreviewUrl } from '../inspectionJump';

function absId(path: string): string {
  return '~' + Buffer.from(path, 'utf8').toString('base64url').replace(/=+$/, '');
}

it('decodes the exact scene raster path from an inspection preview URL', () => {
  const path = 'W:/GSHC2IMPS/PRODUCT/2026/11/19/JL1KF02B02_20261119120000_101_0020_001_L1_PAN/PAN.tif';
  const id = absId(path);
  const url = '/api/scenes/' + encodeURIComponent(id) + '/preview?div=16';
  expect(sceneRefFromPreviewUrl(url)).toEqual({ id, path });
});

it('ignores previews without an absolute-path scene id and non-scene URLs', () => {
  expect(sceneRefFromPreviewUrl('/api/scenes/c2NlbmUtcmVs/preview?div=16')).toBeNull();
  expect(sceneRefFromPreviewUrl('data:image/png;base64,AAAA')).toBeNull();
  expect(sceneRefFromPreviewUrl('')).toBeNull();
  expect(sceneRefFromPreviewUrl(null)).toBeNull();
});
