import {it,expect} from 'vitest';
import {TourCoverage} from '../tourCoverage';
it('only marks the intersection of actually presented layers',()=>{const c=new TourCoverage(4);c.record(0,0,0,.5,1);expect(c.viewed(0,true)).toBe(false);c.record(1,0,0,1,.5);expect(c.viewed(0,true)).toBe(true);expect(c.viewed(8,true)).toBe(false);expect(c.viewed(2,true)).toBe(false);});
it('is bounded, idempotent and clears',()=>{const c=new TourCoverage(4);for(let i=0;i<10000;i++)c.record(0,0,0,1,1);expect(c.a.length).toBe(16);expect(c.a.every(v=>v===1)).toBe(true);c.clear();expect(c.a.some(Boolean)).toBe(false);});
it('does not claim partially displayed cells',()=>{const c=new TourCoverage(4);c.record(0,.01,.01,.1,.1);expect(c.a.some(Boolean)).toBe(false);});
it('never wraps a completely off-image viewport into the next row',()=>{const c=new TourCoverage(4);c.record(0,2,0,1,1);c.record(1,-2,0,.2,1);expect(c.a.some(Boolean)).toBe(false);expect(c.b.some(Boolean)).toBe(false);});
