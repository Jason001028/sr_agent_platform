import type { Annotation } from './spatial';
/** Presentation only: never changes geometry, verification or stored results. */
export function annotationVisual(a: Pick<Annotation,'title'> & {category?:string}) {
 const quality=a.category==='quality' || a.category==='image_quality' || (!a.category && !/^(?:无|未见|未发现|no\b)/i.test(a.title.trim()) && /条带|伪影|噪声|噪点|模糊|过曝|欠曝|坏线|图像质量|artifact|striping|noise|blur|overexpos|underexpos/i.test(a.title));
 return {'--annotation-color':quality?'#B65F55':'#287F8B','--annotation-selected':quality?'#94483F':'#155C66','--annotation-soft':quality?'rgba(182,95,85,.10)':'rgba(40,127,139,.10)'};
}
