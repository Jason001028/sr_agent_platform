import type { Poly } from './maskgen';

export interface AutoDiffOrigin {
  source: 'auto_diff'; analysis_id: string; image_id: number; group_id: string;
  region_id: string; parameter_version: string; initial_points: string; needs_review: boolean;
  reviewed?: boolean;
}
export type OwnedPoly = Poly & { autoDiff?: AutoDiffOrigin };
const signature = (polys: Poly[]) => JSON.stringify(polys.map(p => ({ points: p, origin: (p as OwnedPoly).autoDiff })));
const clone = (polys: Poly[]): Poly[] => polys.map(p => {
  const copy: OwnedPoly = p.map(([x, y]) => [x, y]);
  if ((p as OwnedPoly).autoDiff) copy.autoDiff = { ...(p as OwnedPoly).autoDiff! };
  return copy;
});

/** Independent from list undo; snapshots contain small vectors only, never TIFFs. */
export class MaskHistory {
  private undoEntries: { before: Poly[]; after: Poly[] }[] = [];
  private redoEntries: { before: Poly[]; after: Poly[] }[] = [];
  get canRedo() { return this.redoEntries.length > 0; }
  get canUndo() { return this.undoEntries.length > 0; }
  record(before: Poly[], after: Poly[]) {
    if (signature(before) === signature(after)) return;
    this.undoEntries.push({ before: clone(before), after: clone(after) });
    if (this.undoEntries.length > 40) this.undoEntries.shift();
    this.redoEntries = [];
  }
  undo(current: Poly[]): Poly[] | null {
    const e = this.undoEntries.at(-1);
    if (!e || signature(current) !== signature(e.after)) return null;
    this.undoEntries.pop(); this.redoEntries.push(e);
    return clone(e.before);
  }
  redo(current: Poly[]): Poly[] | null {
    const e = this.redoEntries.at(-1);
    if (!e || signature(current) !== signature(e.before)) return null;
    this.redoEntries.pop(); this.undoEntries.push(e);
    return clone(e.after);
  }
}

/** Preserve manual polygons and any automatic polygon changed by the user.
 * An edited region owns its region_id: regeneration must not duplicate it. */
export function replaceAutoGroup(current: Poly[], incoming: OwnedPoly[], analysisId: string): Poly[] {
  const edited = new Set(current.filter(p => {
    const o = (p as OwnedPoly).autoDiff;
    return o?.analysis_id === analysisId && (o.reviewed === true || JSON.stringify(p) !== o.initial_points);
  }).map(p => (p as OwnedPoly).autoDiff!.region_id));
  return [...current.filter(p => {
    const o = (p as OwnedPoly).autoDiff;
    return !o || o.analysis_id !== analysisId || edited.has(o.region_id);
  }), ...incoming.filter(p => !edited.has(p.autoDiff!.region_id))];
}
