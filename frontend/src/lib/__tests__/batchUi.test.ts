/**
 * batchUi.test.ts — PR F 展示语义（修订 4 / 6）
 * ------------------------------------------------------------------
 * 进度文字必须区分「检测未完成时的暂无需修复项」和「检测完成后的不适用」；
 * DEMO/MOCK 与演示验证不得写成生产成功。
 */
import { describe, expect, it } from 'vitest';
import {
  isolateFeedbackSvg, eventLabel, goalStatusLabel, modeBadge, repairTrackText, trackPercent,
  trackText, verificationNote,
} from '../batchUi';
import type { BatchEvent, BatchProgressTrack, BatchSnapshot } from '../api';

const unknownZero: BatchProgressTrack = {
  done: 0, total: 0, applicable: false, known: false,
};
const settledZero: BatchProgressTrack = {
  done: 0, total: 0, applicable: false, known: true,
};
const running: BatchProgressTrack = {
  done: 3, total: 5, applicable: true, known: true,
};

describe('三维进度文字', () => {
  it('R=0 且检测未完成 → 暂无需修复项·统计中', () => {
    expect(repairTrackText(unknownZero)).toBe('暂无需修复项·统计中');
    expect(trackText(unknownZero)).toBe('统计中');
  });

  it('检测完成后 R=0 → 不适用', () => {
    expect(repairTrackText(settledZero)).toBe('不适用');
    expect(trackText(settledZero)).toBe('不适用');
  });

  it('适用时只显示真实分子分母，不补说明', () => {
    expect(repairTrackText(running)).toBe('');
    expect(trackText(running)).toBe('3 / 5');
  });

  it('比例只给画条用；分母不适用时返回 null', () => {
    expect(trackPercent(running)).toBe(60);
    expect(trackPercent(unknownZero)).toBeNull();
    expect(trackPercent({ ...running, total: 0 })).toBeNull();
    expect(trackPercent({ ...running, done: 99, total: 10 })).toBe(100);
  });
});

function snapshot(overrides: Partial<BatchSnapshot> = {}): BatchSnapshot {
  return {
    contract_version: 'batch_snapshot.v1',
    goal: {
      goal_id: 'g1', session_id: 's1', title: '批次', kind: 'batch',
      status: 'RUNNING', phase: 'detecting', mode: 'production',
      version: 1, control_revision: 0, monitoring_done: false,
      monitor_state: null, last_seq: 10, created_at: 1, updated_at: 2,
      finished_at: null, batch_control: {},
    },
    counts: {
      total: 5, eligible: 5, detected: 3, detection_pending: 2,
      needs_repair: 3, submitted: 2, verified: 1,
      verified_demo: 0, verified_production: 1, verified_other: 0,
      converged: 0, no_issue: 0, failed: 0, blocked: 0, excluded: 0,
      skipped_runtime: 0, cancelled: 0, capacity_skipped: 0,
      unknown_jobs: 0, detecting: 1, submitting: 0, waiting_sr: 1,
      verifying: 0, in_flight_sr: 1,
    },
    progress: {
      detection: { done: 3, total: 5, applicable: true, known: true },
      submission: { done: 2, total: 3, applicable: true, known: true },
      verification: { done: 1, total: 2, applicable: true, known: true },
    },
    producer: {
      current: null, detecting_count: 1, preparing_count: 0, prepared_count: 3,
    },
    active_jobs: [],
    report: { available: false, updated_at: null },
    server_time: 3,
    ...overrides,
  };
}

describe('批次身份与验证语义', () => {
  it('DEMO 必须显眼，不能伪装成生产批次', () => {
    expect(modeBadge(snapshot()).text).toBe('生产批次');
    expect(modeBadge(snapshot({ goal: { ...snapshot().goal, mode: 'demo' } })).text)
      .toBe('DEMO / MOCK');
    expect(modeBadge(null).tone).toBe('unknown');
  });

  it('演示契约验证不写成生产成功', () => {
    const demo = snapshot();
    demo.counts = { ...demo.counts, verified_demo: 2, verified_production: 0 };
    expect(verificationNote(demo)).toContain('不代表生产 SR 成功');
  });

  it('状态未知优先于验证文案', () => {
    const unknown = snapshot();
    unknown.counts = { ...unknown.counts, unknown_jobs: 1, verified_demo: 0 };
    expect(verificationNote(unknown)).toContain('状态未知');
  });

  it('Goal 状态有中文标签，未知值原样返回', () => {
    expect(goalStatusLabel('WAITING_EXTERNAL')).toBe('等待外部超分');
    expect(goalStatusLabel('SOMETHING_NEW')).toBe('SOMETHING_NEW');
  });
});

describe('事件摘要', () => {
  function ev(type: string, payload: Record<string, unknown> = {},
              ordinal?: number): BatchEvent {
    return {
      goal_id: 'g1', seq: 1, event_type: type, payload, created_at: 1,
      step_ordinal: ordinal ?? null,
    };
  }

  it('场景状态变化带序号与方向', () => {
    expect(eventLabel(ev('item_state_changed',
      { from: 'DETECTING', to: 'MASK_READY' }, 7))).toBe('场景 #7 · 检测中 → 掩码就绪');
  });

  it('用户控制与监控更新分开描述', () => {
    expect(eventLabel(ev('batch_user_control', { action: 'pause' }))).toBe('用户暂停批次');
    expect(eventLabel(ev('batch_control_changed', { monitoring_done: true })))
      .toBe('监控更新 · 已收尾');
  });
});


it('isolates SVG styles, keyframes, IDs and reduced-motion selectors per instance', () => {
  const source = '<svg><defs><clipPath id="clip"/></defs><path class="edge" clip-path="url(#clip)"/><style>.edge{animation:draw 1s}@keyframes draw{to{opacity:1}}@media(prefers-reduced-motion:reduce){*{animation:none!important}}</style></svg>';
  const animated = isolateFeedbackSvg(source, 'one', true);
  const still = isolateFeedbackSvg(source, 'two', false);
  expect(animated).toContain('.one-edge{animation:one-draw');
  expect(animated).toContain('@keyframes one-draw');
  expect(animated).toContain('url(#one-clip)');
  expect(animated).toContain('svg[data-feedback-svg="one"] *{');
  expect(still).toContain('.two-edge{animation:none!important}');
  expect(still).not.toContain('one-edge');
});
