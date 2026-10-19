/** Per-machine developer-mode verification persisted in the browser. */
export const DEVELOPER_PASSWORD = 'cgwx';
const KEY_PREFIX = 'sr.developerMode.';

function key(machineId: string): string {
  return KEY_PREFIX + (machineId || 'local');
}

export function isDeveloperMode(machineId = 'local'): boolean {
  try {
    return typeof localStorage !== 'undefined'
      && localStorage.getItem(key(machineId)) === '1';
  } catch {
    return false;
  }
}

export function verifyDeveloperPassword(
  password: string, machineId = 'local',
): boolean {
  if (password !== DEVELOPER_PASSWORD) return false;
  try {
    localStorage.setItem(key(machineId), '1');
  } catch {
    return false;
  }
  return true;
}

export function clearDeveloperMode(machineId = 'local'): void {
  try { localStorage.removeItem(key(machineId)); } catch { /* private mode */ }
}
