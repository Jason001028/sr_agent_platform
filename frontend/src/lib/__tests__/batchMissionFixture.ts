import type { BatchSnapshot } from '../api';
export function missionFixture(): BatchSnapshot {
  return {
    contract_version: 'batch_snapshot.v1',
    goal: { goal_id: 'g', session_id: 's', title: '真实批次', kind: 'batch', status: 'RUNNING', phase: 'detecting', mode: 'production', version: 1, control_revision: 2, monitoring_done: false, monitor_state: 'running', last_seq: 1, created_at: 1, updated_at: 1, finished_at: null, batch_control: {} },
    counts: { total: 27, eligible: 27, detected: 21, detection_pending: 6, needs_repair: 18, submitted: 13, verified: 8, verified_demo: 0, verified_production: 8, verified_other: 0, converged: 8, no_issue: 0, failed: 0, blocked: 0, excluded: 0, skipped_runtime: 0, cancelled: 0, capacity_skipped: 0, unknown_jobs: 0, detecting: 1, submitting: 0, waiting_sr: 4, verifying: 0, in_flight_sr: 4 },
    progress: { detection: { done: 21, total: 27, known: true, applicable: true }, submission: { done: 13, total: 18, known: true, applicable: true }, verification: { done: 8, total: 13, known: true, applicable: true } },
    producer: { current: { item_id: 'i1', ordinal: 1, name: 'scene-one', status: 'DETECTING', item_version: 1, attempt: 0, updated_at: 1, image_path: '/scene/one/PAN.tif', has_mask: false, overlay_ready: false, candidate_count: 0, job_id: null }, detecting_count: 1, preparing_count: 1, prepared_count: 13 },
    active_jobs: [], recent_jobs: [], report: { available: false, updated_at: null }, server_time: 1,
  };
}
