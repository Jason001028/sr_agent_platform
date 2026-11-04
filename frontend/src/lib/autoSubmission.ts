// Reset legacy rollout preferences once; subsequent explicit choices remain persistent.
const KEY = 'sr.submit.automatic.v2';
export function isAutoSubmissionEnabled(): boolean {
  try { return localStorage.getItem(KEY) !== '0'; } catch { return true; }
}
export function saveAutoSubmission(enabled: boolean): void {
  try { localStorage.setItem(KEY, enabled ? '1' : '0'); } catch { /* session preference still works */ }
}
