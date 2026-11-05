export interface TourPoint { x: number; y: number }
export interface TourPlan { points: TourPoint[]; scale: number; width: number; height: number; tw: number; th: number }
export type TourRoute = 'rows' | 'columns' | 'inward';
export const TOUR_ROUTES: readonly {value:TourRoute;label:string;description:string}[] = [
  {value:'rows',label:'横向往返',description:'逐行往返，沿用默认巡览路线'},
  {value:'columns',label:'纵向往返',description:'逐列往返，从左向右覆盖影像'},
  {value:'inward',label:'向内环绕',description:'从外圈逐层向内，覆盖至中心'},
];
/** Shared by the real planner and the SVG cards: every adjacent step is one grid cell. */
export function tourGridOrder(nx:number,ny:number,route:TourRoute='rows'):{x:number;y:number}[] {
  if(!Number.isSafeInteger(nx)||!Number.isSafeInteger(ny)||nx<1||ny<1||nx*ny>4096)throw new Error('路线网格无效或过大');
  const points:{x:number;y:number}[]=[];
  if(route==='rows'){
    for(let y=0;y<ny;y++)for(let i=0;i<nx;i++)points.push({x:y%2?nx-1-i:i,y});
  }else if(route==='columns'){
    for(let x=0;x<nx;x++)for(let i=0;i<ny;i++)points.push({x,y:x%2?ny-1-i:i});
  }else if(route==='inward'){
    let left=0,right=nx-1,top=0,bottom=ny-1;
    while(left<=right&&top<=bottom){
      for(let x=left;x<=right;x++)points.push({x,y:top});top++;
      for(let y=top;y<=bottom;y++)points.push({x:right,y});right--;
      if(top<=bottom){for(let x=right;x>=left;x--)points.push({x,y:bottom});bottom--;}
      if(left<=right){for(let y=bottom;y>=top;y--)points.push({x:left,y});left++;}
    }
  }else throw new Error('未知巡览路线');
  return points;
}
export function planTour(W: number, H: number, tw: number, th: number, cw: number, ch: number,
  region = { x: 0, y: 0, w: W, h: H }, route:TourRoute='rows'): TourPlan {
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
  const points=tourGridOrder(xs.length,ys.length,route).map(({x,y})=>({x:xs[x],y:ys[y]}));
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
