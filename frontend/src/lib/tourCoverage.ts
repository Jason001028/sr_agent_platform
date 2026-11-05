/** Bounded coverage raster. A cell counts only if its whole area was displayed. */
export class TourCoverage {
 readonly a: Uint8Array;
 readonly b: Uint8Array;
 constructor(readonly size=128){this.a=new Uint8Array(size*size);this.b=new Uint8Array(size*size);}
 clear(){this.a.fill(0);this.b.fill(0);}
 record(layer:0|1,x:number,y:number,w:number,h:number){
  if(![x,y,w,h].every(Number.isFinite)||w<=0||h<=0)return;
  const n=this.size,mask=layer===0?this.a:this.b;
  const x0=Math.max(0,Math.ceil(x*n)),y0=Math.max(0,Math.ceil(y*n));
  const x1=Math.min(n,Math.floor((x+w)*n)),y1=Math.min(n,Math.floor((y+h)*n));
  if(x0>=x1||y0>=y1)return;
  for(let j=y0;j<y1;j++)mask.fill(1,j*n+x0,j*n+x1);
 }
 viewed(i:number,compare:boolean){return !!this.a[i]&&(!compare||!!this.b[i]);}
}
