/**
 * notices.test.ts — 「任务跑完了」这条提醒的纯逻辑
 * ------------------------------------------------------------------
 * 锁住三件事：
 *  1. 只有**终态**值得提醒（中间态、CANCELLED、UNKNOWN 都不弹）；
 *  2. 失败条**常驻**（persist），成功条自动消失；
 *  3. 拿不到任务行时**不编目录名**（订阅是应用级常驻的，行可能还没 GET 回来）。
 */
import { describe, it, expect } from 'vitest';
import { noticeKey, jobNotice } from '../notices.js';
import type { JobUpdateEvent, QueueTask } from '../api.js';

function ev(over: Partial<JobUpdateEvent> = {}): JobUpdateEvent {
  return {
    type: 'job_update', task_id: 128, job_id: 9001,
    state: 'COMPLETED', prev_state: 'RUNNING', ok: true, error: null,
    updated_at: 1758500000, started_at: 1758490000, finished_at: 1758500000,
    ...over,
  };
}

function task(over: Partial<QueueTask> = {}): QueueTask {
  return {
    task_id: 128, fingerprint: 'fp', session_id: null, job_id: 9001, state: 'COMPLETED',
    params: {
      lq_path: '/DiskArray/GSHC2IMPS/2026/03/18/GSHC2IMPS_20260318_L1_PAN',
      mask_path: '/DiskArray/GSHC2IMPS/2026/03/18/GSHC2IMPS_20260318_L1_PAN/PAN_mask.tif',
      sr_scale: 2, suffix: '260318', gpu: 0, cloud_limit: 80,
      delete_ori: false, grid_align: true,
    },
    config_xml: null, batch_script: null, log_dir: null,
    created_at: 1758480000, updated_at: 1758500000,
    started_at: 1758490000, finished_at: 1758500000,
    preview_state: null, preview_note: null,
    ...over,
  };
}

describe('noticeKey：只有终态值得提醒', () => {
  it('COMPLETED / FAILED 各给一个键（含 task_id 与状态）', () => {
    expect(noticeKey(ev())).toBe('128:COMPLETED');
    expect(noticeKey(ev({ state: 'FAILED', ok: false }))).toBe('128:FAILED');
  });

  it('小写状态归一（后端大写，但不靠它）', () => {
    expect(noticeKey(ev({ state: 'completed' }))).toBe('128:COMPLETED');
  });

  it('中间态与其它终结态都不弹', () => {
    for (const s of ['SUBMITTING', 'PENDING', 'RUNNING', 'UNKNOWN', 'CANCELLED', '']) {
      expect(noticeKey(ev({ state: s }))).toBeNull();
    }
  });

  it('同一任务的两个终态是两个键（先 FAILED 后补一次 COMPLETED 不会互相顶掉）', () => {
    expect(noticeKey(ev({ state: 'FAILED' }))).not.toBe(noticeKey(ev({ state: 'COMPLETED' })));
  });
});

describe('jobNotice：文案', () => {
  it('完成：标题带 task_id，第二行是场景目录名 · 倍率', () => {
    const n = jobNotice(ev(), task())!;
    expect(n.kind).toBe('ok');
    expect(n.title).toBe('超分完成 #128');
    expect(n.text).toBe('GSHC2IMPS_20260318_L1_PAN · ×2');
    expect(n.reason).toBe('');
    expect(n.persist).toBe(false);        // 成功条自动消失
  });

  it('失败：常驻 + 运行期捕获的原因（比 log_dir 具体）', () => {
    const n = jobNotice(ev({ state: 'FAILED', ok: false, error: '进程退出码 1' }),
      task({ state: 'FAILED' }), '写盘失败：只读文件系统')!;
    expect(n.kind).toBe('fail');
    expect(n.title).toBe('超分失败 #128');
    expect(n.reason).toBe('写盘失败：只读文件系统');
    expect(n.persist).toBe(true);
  });

  it('失败但没有运行期原因：回退「见目录 <log_dir 末段>」', () => {
    const n = jobNotice(ev({ state: 'FAILED', ok: false }),
      task({ state: 'FAILED', log_dir: '/DiskArray/sr_logs/2026/09/22/task_128' }), null)!;
    expect(n.reason).toBe('原因见目录 task_128');
  });

  it('失败且行里什么都没有：回退「原因见队列页」，不编原因', () => {
    const n = jobNotice(ev({ state: 'FAILED' }), null, null)!;
    expect(n.reason).toBe('原因见队列页');
    expect(n.text).toBe('');              // 没有行就不编目录名与倍率
  });
});

describe('jobNotice：拿不到行时不编造', () => {
  it('task=null：只报 task_id', () => {
    const n = jobNotice(ev(), null)!;
    expect(n.title).toBe('超分完成 #128');
    expect(n.text).toBe('');
    expect(n.taskId).toBe(128);
  });

  it('Windows 形态的 lq_path 也取末段', () => {
    const n = jobNotice(ev(), task({
      params: { ...task().params, lq_path: 'W:\\DiskArray\\GF07A03_20240101_L1' },
    }))!;
    expect(n.text).toBe('GF07A03_20240101_L1 · ×2');
  });

  it('尾部分隔符不算末段', () => {
    const n = jobNotice(ev(), task({
      params: { ...task().params, lq_path: '/DiskArray/A/B/' },
    }))!;
    expect(n.text).toBe('B · ×2');
  });

  it('中间态直接给 null（调用方不用再判一次）', () => {
    expect(jobNotice(ev({ state: 'RUNNING' }), task())).toBeNull();
  });

  it('key 与 noticeKey 同源（去重键就是它）', () => {
    expect(jobNotice(ev(), task())!.key).toBe(noticeKey(ev()));
  });
});
