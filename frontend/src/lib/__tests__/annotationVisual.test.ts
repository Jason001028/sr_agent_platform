import {it,expect} from 'vitest';
import {annotationVisual} from '../annotationVisual';
it('uses teal for ordinary terrain without modifying input',()=>{
 const a={title:'Road candidate'};const before=JSON.stringify(a);
 expect(annotationVisual(a)['--annotation-color']).toBe('#287F8B');expect(JSON.stringify(a)).toBe(before);
});
it('uses brick for quality titles and respects explicit categories',()=>{
 expect(annotationVisual({title:'Suspected vertical striping artifact'})['--annotation-color']).toBe('#B65F55');
 expect(annotationVisual({title:'Candidate',category:'image_quality'})['--annotation-color']).toBe('#B65F55');
 expect(annotationVisual({title:'No noise'})['--annotation-color']).toBe('#287F8B');
 expect(annotationVisual({title:'Road',category:'terrain'})['--annotation-color']).toBe('#287F8B');
});
