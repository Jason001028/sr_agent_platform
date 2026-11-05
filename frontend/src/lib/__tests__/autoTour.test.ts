import { describe,it,expect } from 'vitest';
import { planTour,travelFraction,tourGridOrder,TOUR_ROUTES } from '../autoTour';
describe('pixel tour route',()=>{
 it('covers an image with <=85% viewport steps and bounded centers',()=>{
  const p=planTour(16000,8000,4000,2000,1000,600);
  const vw=1000/p.scale*p.width/p.tw,vh=600/p.scale*p.height/p.th;
  expect(p.scale).toBeLessThanOrEqual(1);
  expect(p.points[0].x-vw/2).toBeCloseTo(0);
  expect(p.points.at(-1)!.y+vh/2).toBeCloseTo(8000);
  for(let i=0;i<p.points.length;i++){
   const a=p.points[i];expect(a.x-vw/2).toBeGreaterThanOrEqual(-.0001);expect(a.y-vh/2).toBeGreaterThanOrEqual(-.0001);
   expect(a.x+vw/2).toBeLessThanOrEqual(16000.001);expect(a.y+vh/2).toBeLessThanOrEqual(8000.001);
   if(i){const b=p.points[i-1];expect(Math.abs(a.x-b.x)).toBeLessThanOrEqual(vw*.85+.001);expect(Math.abs(a.y-b.y)).toBeLessThanOrEqual(vh*.85+.001);}
  }
 });
 it('clips regions, handles extreme aspect ratios, rejects small or invalid images',()=>{
  expect(planTour(100000,100,10000,10,800,600).points.length).toBeGreaterThan(1);
  expect(()=>planTour(100,100,100,100,800,600)).toThrow();
  expect(()=>planTour(NaN,100,100,100,800,600)).toThrow();
  const p=planTour(16000,8000,4000,2000,1000,600,{x:-100,y:0,w:10000,h:8000});expect(p.points[0].x).toBeGreaterThan(0);
 });
 it('uses time based cruise speed and eased endpoints',()=>{
  const d=1100,s=110;
  expect(travelFraction(2,d,s).fraction-travelFraction(1,d,s).fraction).toBeCloseTo(.1);
  expect(travelFraction(.1,d,s).fraction).toBeLessThan(.01);
  expect(travelFraction(100,d,s).fraction).toBe(1);
 });
 it('preserves the existing horizontal snake as the default',()=>{
  const baseline=planTour(16000,8000,4000,2000,1000,600);
  expect(planTour(16000,8000,4000,2000,1000,600,undefined,'rows')).toEqual(baseline);
  const xs=[...new Set(baseline.points.map(p=>p.x))],ys=[...new Set(baseline.points.map(p=>p.y))];
  expect(baseline.points).toEqual(ys.flatMap((y,i)=>(i%2?[...xs].reverse():xs).map(x=>({x,y}))));
 });
 it.each(TOUR_ROUTES)('$label covers the same grid exactly once with adjacent steps',({value})=>{
  for(const [nx,ny] of [[5,4],[4,5],[1,7],[8,1],[1,1],[64,64]]){
   const points=tourGridOrder(nx,ny,value);
   expect(points).toHaveLength(nx*ny);expect(new Set(points.map(p=>`${p.x},${p.y}`)).size).toBe(nx*ny);
   for(let i=0;i<points.length;i++){
    const p=points[i];expect(p.x>=0&&p.x<nx&&p.y>=0&&p.y<ny).toBe(true);
    if(i)expect(Math.abs(p.x-points[i-1].x)+Math.abs(p.y-points[i-1].y)).toBe(1);
   }
  }
  const p=planTour(16000,8000,4000,2000,1000,600,{x:1000,y:1000,w:12000,h:6000},value);
  const baseline=planTour(16000,8000,4000,2000,1000,600,{x:1000,y:1000,w:12000,h:6000});
  expect(new Set(p.points.map(p=>JSON.stringify(p)))).toEqual(new Set(baseline.points.map(p=>JSON.stringify(p))));
 });
 it('distinguishes route directions and bounds the schematic grid',()=>{
  expect(tourGridOrder(4,4,'columns').slice(0,3)).toEqual([{x:0,y:0},{x:0,y:1},{x:0,y:2}]);
  expect(tourGridOrder(4,4,'inward').slice(-4)).toEqual([{x:1,y:1},{x:2,y:1},{x:2,y:2},{x:1,y:2}]);
  expect(()=>tourGridOrder(100,100)).toThrow();expect(()=>tourGridOrder(0,3)).toThrow();
 });
});
