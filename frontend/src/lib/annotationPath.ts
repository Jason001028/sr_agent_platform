import type { XY } from './spatial';
/** Corners are already projected in clockwise order. Never mutate source coordinates. */
export function rectanglePath(corners: XY[], anchor: XY) {
 if(corners.length!==4 || ![...corners,anchor].every(p=>p.every(Number.isFinite)))return {d:'',connector:''};
 let start=0,best=Infinity;
 corners.forEach((p,i)=>{const d=(p[0]-anchor[0])**2+(p[1]-anchor[1])**2;if(d<best){best=d;start=i;}});
 const ordered=corners.map((_,i)=>corners[(start+i)%4]);
 return {d:'M '+ordered.map(p=>p.join(' ')).join(' L ')+' Z',connector:best>16?'M '+anchor.join(' ')+' L '+ordered[0].join(' '):''};
}
