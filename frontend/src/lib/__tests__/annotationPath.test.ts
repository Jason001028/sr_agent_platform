import {it,expect} from 'vitest';
import {rectanglePath} from '../annotationPath';
import type {XY} from '../spatial';
it('starts top left and closes clockwise without mutating points',()=>{
 const p:XY[]=[[0,0],[100,0],[100,50],[0,50]];const snapshot=JSON.stringify(p);
 expect(rectanglePath(p,[0,0])).toEqual({d:'M 0 0 L 100 0 L 100 50 L 0 50 Z',connector:''});
 expect(JSON.stringify(p)).toBe(snapshot);
});
it('starts at nearest corner and supplies connector for a detached anchor',()=>{
 const p:XY[]=[[0,0],[100,0],[100,50],[0,50]];
 expect(rectanglePath(p,[110,60])).toEqual({d:'M 100 50 L 0 50 L 0 0 L 100 0 Z',connector:'M 110 60 L 100 50'});
 expect(rectanglePath(p,[-1,49]).connector).toBe('');
});
