import { describe, it, expect, vi } from 'vitest';
import { cellStyle, createLogoPlayer } from '../asciiLogoFlow';
import { LOGO_CYCLE_MS } from '../asciiLogoFrames';
describe('ASCII logo flow', () => {
 it('is periodic, stays visible and varies within each cycle', () => {
  const first=cellStyle(100,160,.8,0);
  expect(cellStyle(100,160,.8,LOGO_CYCLE_MS)).toEqual(first);
  expect(cellStyle(100,160,.8,LOGO_CYCLE_MS*2)).toEqual(first);
  const states=Array.from({length:72},(_,i)=>cellStyle(100,160,.8,i*100));
  expect(new Set(states.map(s=>s.glyph)).size).toBeGreaterThan(3);
  expect(states.every(s=>s.alpha>.4 && s.alpha<.8)).toBe(true);
 });
 it('limits drawing to 15FPS, cancels and resumes without hidden-time catch-up', () => {
  const pending=new Map<number,FrameRequestCallback>();let id=0;
  const request=(cb:FrameRequestCallback)=>{pending.set(++id,cb);return id;};
  const cancel=(n:number)=>{pending.delete(n);};
  const paint=vi.fn();const player=createLogoPlayer(paint,request,cancel);
  function tick(time:number) {const [n,cb]=[...pending][0];pending.delete(n);cb(time);}
  player.start();player.start();expect(pending.size).toBe(1);
  for(let t=0;t<1000;t+=10)tick(t);
  expect(paint.mock.calls.length).toBe(15);
  player.stop();expect(pending.size).toBe(0);
  const last=paint.mock.calls.at(-1)![0];
  player.start();tick(9000);tick(9100);
  expect(paint.mock.calls.at(-1)![0]-last).toBeLessThan(200);
  player.stop();expect(pending.size).toBe(0);
 });
});
