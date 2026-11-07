import { describe, expect, it } from 'vitest';
import { focusMissionItems, missionItemLabel, missionItems, missionSummary, missionTrackText, recordMissionItem } from '../batchMission';
import { missionFixture } from './batchMissionFixture';
describe('Quiet Mission authoritative projection', () => {
  it('keeps stage-specific numerators/denominators, never a synthetic total ratio', () => {
    const s = missionFixture();
    expect(missionSummary(s)).toMatchObject({ value: '21 / 27', label: '检测进度' });
    s.counts.detecting = 0; s.goal.phase = 'monitoring';
    expect(missionSummary(s)).toMatchObject({ value: '13 / 18', label: 'SR 提交进度' });
    s.goal.phase = 'verifying'; s.counts.verifying = 1;
    expect(missionSummary(s)).toMatchObject({ value: '8 / 13', label: '验收进度' });
  });
  it('distinguishes unknown, not applicable and a real zero', () => {
    expect(missionTrackText({ done: 0, total: 0, known: false, applicable: false })).toBe('统计中');
    expect(missionTrackText({ done: 0, total: 0, known: true, applicable: false })).toBe('—');
    expect(missionTrackText({ done: 0, total: 27, known: true, applicable: true })).toBe('0 / 27');
  });
  it('27 failures before any SR preserve pre-job semantics and each item identity', () => {
    const s = missionFixture(); s.goal.status = 'BLOCKED'; s.goal.phase = 'blocked';
    Object.assign(s.counts, { failed: 27, submitted: 0, detected: 0, detecting: 0, in_flight_sr: 0 });
    s.producer.current = null;
    s.recent_jobs = Array.from({ length: 27 }, (_, n) => ({ item_id: 'i' + n, ordinal: n + 1, name: 'scene-' + n, status: 'FAILED', item_version: 1, attempt: 0, job_id: null, unknown: false, error: 'identical cause' }));
    expect(missionSummary(s)).toMatchObject({ value: '27', unit: '景异常', label: '异常景数', issueFocus: true });
    expect(missionSummary(s).noJobNotice).toContain('尚无已提交的 SR');
    const focus = focusMissionItems(s);
    expect(focus).toHaveLength(3);
    expect(new Set(missionItems(s).map(i => i.item_id)).size).toBe(27);
    expect(focus.every(i => missionItemLabel(i).includes('未生成作业'))).toBe(true);
  });
  it('deduplicates same items and lets active/current facts replace old recent facts', () => {
    const s = missionFixture(), row = { ...s.producer.current!, unknown: false };
    s.recent_jobs = [{ ...row, status: 'FAILED' }];
    s.active_jobs = [{ ...row, status: 'WAITING_SR', job_id: 12 }];
    expect(missionItems(s)).toHaveLength(1);
    expect(missionItems(s)[0].status).toBe('DETECTING');
    expect(focusMissionItems(s)).toHaveLength(1);
  });
  it('completed batches have no expired current item and label clean scenes honestly', () => {
    const s = missionFixture(); s.goal.status = 'COMPLETED';
    expect(focusMissionItems(s)).toEqual([]);
    expect(missionSummary(s)).toMatchObject({ value: '8', label: '已验收' });
    s.counts.verified = 0; s.counts.no_issue = 27;
    expect(missionSummary(s)).toMatchObject({ value: '27', label: '无需修复' });
  });
  it('does not render unknown backend enums, and reads real job identity from records', () => {
    expect(missionItemLabel({ item_id: 'x', ordinal: 1, name: '', status: 'BRAND_NEW', attempt: 0 })).toBe('状态待确认');
    expect(recordMissionItem({ item_id: 'x', ordinal: 1, name: 'x', status: 'FAILED', version: 1, external_job_id: 5, checkpoint: { job_id: 4 } }).job_id).toBe(5);
  });
  it('does not disguise paused or cancelled goals as a generic failure', () => {
    const s = missionFixture(); s.producer.current = null; s.counts.failed = 2;
    s.goal.status = 'PAUSED';
    expect(missionSummary(s)).toMatchObject({ state: '已暂停', issueFocus: true });
    s.goal.status = 'CANCELLED';
    expect(missionSummary(s)).toMatchObject({ state: '已取消', issueFocus: false });
    expect(focusMissionItems(s)).toEqual([]);
  });
});
