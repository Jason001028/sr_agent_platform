import { HttpError } from './api';
import type { QueueSubmitResult } from './api';

export function receiptKind(r: QueueSubmitResult): 'new' | 'existing' | 'unknown' {
  if (!Number.isSafeInteger(r.task_id) || r.task_id <= 0) return 'unknown';
  if (r.status === 'SUBMITTED') return 'new';
  if (['RESUMED_ACTIVE', 'RESUMED_COMPLETED'].includes(r.status)) return 'existing';
  return 'unknown';
}

export function submissionUnknown(e: unknown): boolean {
  return !(e instanceof HttpError) || e.status >= 500 || [408, 409].includes(e.status);
}
