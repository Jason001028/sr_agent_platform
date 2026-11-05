export type ViewerBackground = 'dots' | 'grid' | 'studio' | 'graphite';
export const VIEWER_BACKGROUND_KEY = 'sr.viewer.background';
export const VIEWER_BACKGROUNDS: readonly {value:ViewerBackground;label:string}[] = [
 {value:'dots',label:'极淡微点阵'},
 {value:'grid',label:'工程网格（默认）'},
 {value:'studio',label:'柔和摄影棚'},
 {value:'graphite',label:'石墨灰'},
];
export function isViewerBackground(value:unknown):value is ViewerBackground {
 return VIEWER_BACKGROUNDS.some(option=>option.value===value);
}
export function readViewerBackground():ViewerBackground {
 try {const saved=localStorage.getItem(VIEWER_BACKGROUND_KEY);return isViewerBackground(saved)?saved:'grid';}
 catch {return 'grid';}
}
export function saveViewerBackground(value:ViewerBackground):void {
 try {localStorage.setItem(VIEWER_BACKGROUND_KEY,value);} catch {/* Keep the session preference. */}
}
