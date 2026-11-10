import { afterEach, it,expect } from 'vitest';
import {
  applyPreviewTrueMaxEdge, getPreviewTrueMaxEdge, imagePreviewDiv, isTruePreviewEdge,
  previewResolutionNote, previewTrueMaxPixels, PREVIEW_TRUE_MAX_EDGE,
  PREVIEW_TRUE_MAX_EDGE_OPTIONS,
} from '../previewLevel';
afterEach(() => { applyPreviewTrueMaxEdge(PREVIEW_TRUE_MAX_EDGE); });
it('treats long edge 16384 as the true-resolution boundary',()=>{
  expect(PREVIEW_TRUE_MAX_EDGE).toBe(16384);
  expect(isTruePreviewEdge(16384)).toBe(true);
  expect(isTruePreviewEdge(16385)).toBe(false);
  expect(isTruePreviewEdge(0)).toBe(false);
});
it('applies the platform setting without accepting arbitrary edges',()=>{
  expect(PREVIEW_TRUE_MAX_EDGE_OPTIONS).toEqual([4096, 8192, 16384]);
  expect(getPreviewTrueMaxEdge()).toBe(16384);
  expect(applyPreviewTrueMaxEdge(8192)).toBe(true);
  expect(getPreviewTrueMaxEdge()).toBe(8192);
  expect(previewTrueMaxPixels()).toBe(8192 * 8192);
  expect(isTruePreviewEdge(8192)).toBe(true);
  expect(isTruePreviewEdge(8193)).toBe(false);
  expect(applyPreviewTrueMaxEdge(5000)).toBe(false);
  expect(getPreviewTrueMaxEdge()).toBe(8192); // 非法值不改当前值
});
it('reports the actual ratio and whether the edge is a true-resolution tier',()=>{
  expect(previewResolutionNote(48000,40000,12000,10000)).toBe('实际约 1/4（长边 12000，真分辨率）');
  expect(previewResolutionNote(48000,40000,16384,13653)).toBe('实际约 1/2.93（长边 16384，真分辨率）');
  expect(previewResolutionNote(48000,40000,20000,16667)).toBe('实际约 1/2.4（长边 20000，超过 16384 上限）');
  expect(previewResolutionNote(48000,40000,0,0)).toBe('');
});
it('uses the configured limit in the status note',()=>{
  applyPreviewTrueMaxEdge(4096);
  expect(previewResolutionNote(48000,40000,4096,3413))
    .toBe('实际约 1/11.72（长边 4096，真分辨率）');
  expect(previewResolutionNote(48000,40000,8000,6667))
    .toBe('实际约 1/6（长边 8000，超过 4096 上限）');
});
it('uses half-size NOSR by default and full-size NOSR with PAN half/full tiers',()=>{
  for(const selected of [32,16,8,4])expect(imagePreviewDiv(selected,'nosr',{W:12000,H:10000},{W:48000,H:40000})).toBe(2);
  for(const selected of [2,1])expect(imagePreviewDiv(selected,'nosr',{W:12000,H:10000},{W:48000,H:40000})).toBe(1);
});
it('never infers lower resolution from a name, file size, unknown dimensions or fewer total pixels alone',()=>{
  for(const image of [{W:48000,H:40000},{W:50000,H:1000},{W:0,H:1000},{W:12000,H:NaN}])
    expect(imagePreviewDiv(4,'nosr',image,{W:48000,H:40000})).toBe(4);
  expect(imagePreviewDiv(4,'nosr',{W:12000,H:10000})).toBe(4);
  for(const kind of ['input','product',undefined])expect(imagePreviewDiv(4,kind,{W:12000,H:10000},{W:48000,H:40000})).toBe(4);
});
