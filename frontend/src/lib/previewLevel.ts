export interface ImageSize { W:number;H:number }
/** Only authoritative same-scene dimensions can opt NOSR into a sharper tier. */
export function imagePreviewDiv(selected:number,kind:string|undefined,image:ImageSize,pan?:ImageSize) {
  if(kind!=='nosr'||!pan||![image.W,image.H,pan.W,pan.H].every(n=>Number.isFinite(n)&&n>0))return selected;
  if(image.W>pan.W||image.H>pan.H||(image.W===pan.W&&image.H===pan.H))return selected;
  return selected<=2?1:2;
}
