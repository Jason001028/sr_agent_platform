const KEY = 'sr.viewer.coordinateHelp.hidden';
let shownThisSession = false;
export function coordinateHelpHidden(): boolean {
  try { return localStorage.getItem(KEY) === '1'; } catch { return false; }
}
export function hideCoordinateHelp(hidden: boolean): void {
  try { localStorage.setItem(KEY, hidden ? '1' : '0'); } catch { /* session guard still applies */ }
}
export function claimCoordinateHelp(): boolean {
  if (shownThisSession || coordinateHelpHidden()) return false;
  shownThisSession = true;
  return true;
}
