// Keep this presentation predicate independent of the viewer state machine.
export function isViewerEmpty(state: {
  compareMode: string; busy: boolean; overlay: { visible: boolean };
  panes: readonly { rec: unknown }[];
}): boolean {
  return state.compareMode !== 'split' && !state.busy && !state.overlay.visible
    && state.panes.every(p => !p.rec);
}
