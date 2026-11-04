export interface TourPoint { x: number; y: number }
export interface TourPlan { points: TourPoint[]; scale: number; width: number; height: number; tw: number; th: number }
export function planTour(W: number, H: number, tw: number, th: number, cw: number, ch: number,
  region = { x: 0, y: 0, w: W, h: H }): TourPlan {
  if (![W,H,tw,th,cw,ch,region.x,region.y,region.w,region.h].every(Number.isFinite) || Math.min(W,H,tw,th,cw,ch,region.w,region.h)<=0) throw new Error('影像或视口尺寸无效');
  const scale = Math.min(1, Math.min(cw/tw,ch/th)*3);
  const vw=cw/scale*W/tw, vh=ch/scale*H/th;
  const x0=Math.max(0,region.x), y0=Math.max(0,region.y);
  const x1=Math.min(W,region.x+region.w), y1=Math.min(H,region.y+region.h);
  if(x1<=x0||y1<=y0) throw new Error('目标区域不在影像内');
  function centers(lo:number,hi:number,span:number,extent:number) {
    const clamp=(v:number)=>span>=extent?extent/2:Math.max(span/2,Math.min(extent-span/2,v));
    if(hi-lo<=span)return [clamp((lo+hi)/2)];
    const n=Math.ceil((hi-lo-span)/(span*.85));
    if(n>4096)throw new Error('路线过长，请缩小巡览范围');
    return Array.from({length:n+1},(_,i)=>clamp(lo+span/2+(hi-lo-span)*i/n));
  }
  const xs=centers(x0,x1,vw,W), ys=centers(y0,y1,vh,H);
  if(xs.length*ys.length>4096)throw new Error('路线过长，请缩小巡览范围');
  const points=ys.flatMap((y,i)=>(i%2?[...xs].reverse():xs).map(x=>({x,y})));
  if(points.length<2)throw new Error('当前预览或目标区域较小，无需巡览；请使用手动导航');
  return {points,scale,width:W,height:H,tw,th};
}
// Constant cruise with 300ms acceleration/deceleration at the endpoints.
export function travelFraction(elapsed:number, distance:number, speed:number) {
  const ramp=Math.min(.3,distance/speed), duration=distance/speed+ramp;
  const t=Math.max(0,Math.min(duration,elapsed));
  const d=t<ramp?speed*t*t/(2*ramp):t>duration-ramp?distance-speed*(duration-t)**2/(2*ramp):speed*(t-ramp/2);
  return {fraction: distance?Math.min(1,d/distance):1, duration};
}
export const TOUR_SPEEDS = { slow:70, normal:110, fast:160 };
