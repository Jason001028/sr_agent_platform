import { it,expect } from 'vitest';
import { imagePreviewDiv } from '../previewLevel';
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
