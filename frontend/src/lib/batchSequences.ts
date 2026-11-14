import type { ViewerRec } from '../stores/viewer';
import type { BatchImportResult } from './api';
export interface RepairSequence {
  key: string; ordinal: number; name: string; path: string;
  count: number; reason: string; checked: boolean; loading: boolean;
}
/** A Viewer number represents one scene directory. The path only names an input
 *  candidate (a directory hint); the backend resolves the current body from
 *  meta.xml (RC: PAN.tif, SC: <目录名>.tif) before comparing it with the NOSR. */
export function viewerRepairSequences(recs: readonly ViewerRec[], ordinal: (rec: ViewerRec) => number): RepairSequence[] {
  const groups = new Map<string, RepairSequence>();
  for (const rec of recs) {
    if (!rec.sceneDir) continue;
    const directory = rec.sceneDir.replace(/\\/g, '/').replace(/\/+$/, '');
    if (groups.has(directory)) continue;
    groups.set(directory, { key: directory, ordinal: ordinal(rec), name: directory.split('/').pop()!,
      path: directory + '/PAN.tif', count: 0, reason: '', checked: false, loading: true });
  }
  return [...groups.values()].sort((a, b) => a.ordinal - b.ordinal);
}
export function importProblem(result: BatchImportResult): string {
  return result.rejected || result.counts.errors || result.diagnostics.some(d => d.severity === 'error') || !result.counts.items
    ? result.diagnostics.filter(d => d.severity === 'error').map(d => d.message).join('；') || '没有符合条件的 PAN / NOSR 配对'
    : '';
}
export function compactBatchError(message: string): string {
  if (message.includes("demo batch requires SR_EXECUTOR=slurm")) return '当前后端使用本地执行器，演示计划需要 Fake Slurm 环境；计划已保留，尚未启动任务。';
  if (message.includes('production batch execution is not enabled yet')) return '生产批量执行尚未开放，当前后端拒绝启动。';
  return message.replace(/(?:[A-Za-z]:[\\/]|\/)[^\s；，：]+/g, path => `影像文件（${path.split(/[\\/]/).pop()}）`);
}
