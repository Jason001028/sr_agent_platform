import { afterEach, describe, expect, it, vi } from 'vitest';
import { isAutoSubmissionEnabled, saveAutoSubmission } from '../autoSubmission';
afterEach(() => vi.unstubAllGlobals());
describe('automatic submission preference', () => {
  it('defaults on and persists an explicit opt out', () => {
    const data = new Map<string, string>([['sr.submit.automatic', '0']]);
    vi.stubGlobal('localStorage', { getItem: (k: string) => data.get(k) ?? null,
      setItem: (k: string, v: string) => data.set(k, v) });
    expect(isAutoSubmissionEnabled()).toBe(true);
    saveAutoSubmission(false);
    expect(isAutoSubmissionEnabled()).toBe(false);
    saveAutoSubmission(true);
    expect(isAutoSubmissionEnabled()).toBe(true);
  });
  it('keeps the default when storage is unavailable', () => {
    vi.stubGlobal('localStorage', { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } });
    expect(isAutoSubmissionEnabled()).toBe(true);
    expect(() => saveAutoSubmission(false)).not.toThrow();
  });
});
