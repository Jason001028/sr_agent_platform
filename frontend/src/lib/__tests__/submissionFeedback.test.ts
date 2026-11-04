import { describe, it, expect } from 'vitest';
import { receiptKind, submissionUnknown } from '../submissionFeedback';
import { HttpError } from '../api';
import type { QueueSubmitResult } from '../api';
const receipt = (status: string, task_id = 42) => ({ status, task_id } as QueueSubmitResult);
describe('submission receipt authority', () => {
  it('accepts persisted submission only with a real task identity', () => {
    expect(receiptKind(receipt('SUBMITTED'))).toBe('new');
    expect(receiptKind(receipt('SUBMITTED', 0))).toBe('unknown');
    expect(receiptKind(receipt('OK'))).toBe('unknown');
  });
  it('distinguishes deduplication from a new queue entry', () => {
    for (const s of ['RESUMED_ACTIVE', 'RESUMED_COMPLETED']) expect(receiptKind(receipt(s))).toBe('existing');
  });
  it('never treats a lost response or ambiguous conflict as a rejection', () => {
    for (const e of [new TypeError('network'), new HttpError(408, 'timeout'), new HttpError(409, 'interrupted'), new HttpError(502, 'gateway')]) expect(submissionUnknown(e)).toBe(true);
    expect(submissionUnknown(new HttpError(422, 'invalid mask'))).toBe(false);
  });
});
