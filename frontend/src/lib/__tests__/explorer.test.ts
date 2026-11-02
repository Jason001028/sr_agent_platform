import { describe, expect, it } from 'vitest';
import { explorerUri } from '../explorer';

describe('Explorer client protocol', () => {
  it('preserves Unicode, spaces and shell metacharacters as data', () => {
    const directory = '/DiskArray/中文 场景/a&b$(test)';
    const uri = explorerUri(directory);
    const encoded = uri.split('/').pop()!.replace(/-/g, '+').replace(/_/g, '/');
    expect(new TextDecoder().decode(Uint8Array.from(atob(encoded), c => c.charCodeAt(0)))).toBe(directory);
    expect(uri).toMatch(/^sr-explorer:\/\/open\/[A-Za-z0-9_-]+$/);
  });
  it.each(['', '/', 'W:/scene', '../scene', '/DiskArray/../secret', '//server/share', '/DiskArray/./scene', '/DiskArray/"x', '/DiskArray/\nx', '/DiskArray/a?x'])('rejects invalid directory %j', path => {
    expect(() => explorerUri(path)).toThrow();
  });
  it('normalizes a trailing separator without changing directory identity', () => {
    expect(explorerUri('/DiskArray/scene/')).toBe(explorerUri('/DiskArray/scene'));
  });
});
