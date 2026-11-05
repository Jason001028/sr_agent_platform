import type {CompareMode} from './compare';
export interface HistoryPane {
 side:'A'|'B';id:number|null;revision:string;cx:number;cy:number;zoom:number;
}
export interface ViewHistoryEntry {
 mode:CompareMode;active:'A'|'B';panes:HistoryPane[];altId?:number;altRevision?:string;showingAlt?:boolean;
}
export function sameHistoryView(a:ViewHistoryEntry,b:ViewHistoryEntry):boolean {
 return a.mode===b.mode&&a.active===b.active&&a.altId===b.altId&&a.altRevision===b.altRevision&&a.showingAlt===b.showingAlt&&a.panes.length===b.panes.length&&a.panes.every((p,i)=>{
  const q=b.panes[i];return p.side===q.side&&p.id===q.id&&p.revision===q.revision&&Math.abs(p.cx-q.cx)<1e-6&&Math.abs(p.cy-q.cy)<1e-6&&Math.abs(p.zoom-q.zoom)<Math.max(1e-8,p.zoom*1e-6);
 });
}
