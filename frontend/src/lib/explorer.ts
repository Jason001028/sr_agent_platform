/** The client handler maps this server directory to its own Windows mount. */
export function explorerUri(directory: string): string {
  const path = directory.replace(/\\/g, '/').replace(/\/+$/, '');
  if (!path || path.length > 4096 || /[\x00-\x1f\x7f"<>|?*:]/.test(path)
      || !path.startsWith('/')
      || path.startsWith('//') || path.split('/').some(p => p === '..' || p === '.')) {
    throw new Error('没有可打开的关联目录');
  }
  const bytes = new TextEncoder().encode(path);
  const payload = btoa(Array.from(bytes, b => String.fromCharCode(b)).join(''))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `sr-explorer://open/${payload}`;
}

export const EXPLORER_READY_KEY = 'sr-explorer:configured:v1';
