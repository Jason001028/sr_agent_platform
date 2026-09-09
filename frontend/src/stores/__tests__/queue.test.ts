/**
 * queue.test.ts — 队列 store 纯函数（表单归一 / SSE 归并 / 状态徽标）
 * ------------------------------------------------------------------
 * Node 环境：只测纯函数（不触碰 pinia store）。覆盖 api-contract.md §3.3 提交
 * body 归一（空 mask_path → null、数值夹取）与 job_update 按 task_id 归并。
 */
import { describe, it, expect } from 'vitest';
import {
  defaultForm, draftToForm, formToSubmit, mergeJobUpdate, stateTone,
  tasksForScene, normDir, pathLeafOf,
} from '../queue.js';
import type { QueueDraft } from '../queue.js';
import type { QueueTask } from '../../lib/api.js';

function task(over: Partial<QueueTask> = {}): QueueTask {
  return {
    task_id: 1, fingerprint: 'fp', session_id: null, job_id: 101,
    state: 'PENDING',
    params: {
      lq_path: '/DiskArray/x', mask_path: null, sr_scale: 2, suffix: '',
      gpu: 0, cloud_limit: 80, delete_ori: false, grid_align: true,
    },
    config_xml: null, batch_script: null, log_dir: null,
    created_at: 0, updated_at: 0,
    ...over,
  };
}

describe('默认值（镜像后端 run_sr 缺省）', () => {
  it('sr_scale=2 / cloud_limit=80 / grid_align=true', () => {
    expect(defaultForm()).toEqual({
      lq_path: '', mask_path: '', sr_scale: 2, suffix: '',
      gpu: 0, cloud_limit: 80, delete_ori: false, grid_align: true,
    });
  });
});

describe('掩码 task_draft → 表单', () => {
  it('null mask → 空串；值原样透传', () => {
    const d: QueueDraft = {
      lq_path: '/DiskArray/GF07A03_xxx_L1_PAN',
      mask_path: '/DiskArray/GF07A03_xxx_L1_PAN/GF07A03_xxx_mask.tif',
      sr_scale: 3, suffix: 't', gpu: 1, cloud_limit: 90,
      delete_ori: false, grid_align: true,
    };
    const f = draftToForm(d);
    expect(f.mask_path).toBe(d.mask_path);
    expect(f.sr_scale).toBe(3);

    const noMask = draftToForm({ ...d, mask_path: null });
    expect(noMask.mask_path).toBe('');
  });
});

describe('formToSubmit（提交 body 归一）', () => {
  it('trim + 空 mask → null + 数值夹取', () => {
    const body = formToSubmit({
      lq_path: '  /DiskArray/x  ', mask_path: '   ', sr_scale: 2, suffix: '',
      gpu: 0, cloud_limit: 80, delete_ori: false, grid_align: true,
    });
    expect(body.lq_path).toBe('/DiskArray/x');
    expect(body.mask_path).toBeNull();
    expect(body.suffix).toBe('');
    expect(body.grid_align).toBe(true);
    expect(body.delete_ori).toBe(false);
  });

  it('超界数值夹到合法域（sr_scale 0→1、8→8、cloud_limit 150→100）', () => {
    const body = formToSubmit({
      lq_path: '/a', mask_path: '', sr_scale: 0, suffix: 't',
      gpu: -2, cloud_limit: 150, delete_ori: true, grid_align: false,
    });
    expect(body.sr_scale).toBe(1);
    expect(body.gpu).toBe(0);
    expect(body.cloud_limit).toBe(100);
    expect(body.suffix).toBe('t');
  });

  it('NaN → 各自默认', () => {
    const body = formToSubmit({
      lq_path: '/a', mask_path: '', sr_scale: Number.NaN, suffix: '',
      gpu: Number.NaN, cloud_limit: Number.NaN, delete_ori: false, grid_align: true,
    });
    expect(body.sr_scale).toBe(2);
    expect(body.gpu).toBe(0);
    expect(body.cloud_limit).toBe(80);
  });
});

describe('mergeJobUpdate（SSE 归并）', () => {
  it('task_id 匹配 → 覆盖 state，其余行不动', () => {
    const rows = [task({ task_id: 1, state: 'PENDING' }),
                  task({ task_id: 2, state: 'RUNNING' })];
    const next = mergeJobUpdate(rows, {
      type: 'job_update', task_id: 1, job_id: 101,
      state: 'RUNNING', prev_state: 'PENDING', ok: true, error: null,
    });
    expect(next[0].state).toBe('RUNNING');
    expect(next[1].state).toBe('RUNNING');        // task 2 不受影响
    expect(rows[0].state).toBe('PENDING');        // 不可变：原数组未动
  });

  it('无匹配 task → 原数组引用不变', () => {
    const rows = [task()];
    expect(mergeJobUpdate(rows, {
      type: 'job_update', task_id: 99, job_id: 1,
      state: 'FAILED', prev_state: 'RUNNING', ok: false, error: 'exit 1',
    })).toBe(rows);
  });

  it('state 推进序列覆盖状态机', () => {
    let rows = [task({ task_id: 1, state: 'SUBMITTING' })];
    for (const st of ['PENDING', 'RUNNING', 'COMPLETED']) {
      rows = mergeJobUpdate(rows, {
        type: 'job_update', task_id: 1, job_id: 101,
        state: st, prev_state: rows[0].state, ok: true, error: null,
      });
      expect(rows[0].state).toBe(st);
    }
  });
});

describe('stateTone（徽标样式映射）', () => {
  it('§3.3 六态 → pending/run/ok/fail/muted', () => {
    expect(stateTone('SUBMITTING')).toBe('pending');
    expect(stateTone('PENDING')).toBe('pending');
    expect(stateTone('RUNNING')).toBe('run');
    expect(stateTone('COMPLETED')).toBe('ok');
    expect(stateTone('FAILED')).toBe('fail');
    expect(stateTone('UNKNOWN')).toBe('muted');
  });
});

/* ---------------- 阶段6：tasksForScene（viewer 任务区关联当前场景） ---------------- */
function sTask(id: number, lq: string, mask: string | null, created: number): QueueTask {
  return task({
    task_id: id, created_at: created, updated_at: created,
    params: {
      lq_path: lq, mask_path: mask, sr_scale: 2, suffix: '',
      gpu: 0, cloud_limit: 80, delete_ori: false, grid_align: true,
    },
  });
}

describe('tasksForScene（按 lq_path + <stem>_mask.tif 关联场景任务）', () => {
  it('同目录同掩码命中；过滤别家掩码/无掩码/他目录，按 created_at 倒序', () => {
    const rows = [
      sTask(1, '/DiskArray/A', '/DiskArray/A/GF07_mask.tif', 10),
      sTask(2, '/DiskArray/A/', '/DiskArray/A/GF07_mask.tif', 30),  // 尾分隔符差异 → 命中
      sTask(3, '/DiskArray/A', '/DiskArray/A/L8_mask.tif', 20),     // 别家场景任务 → 剔除
      sTask(4, '/DiskArray/A', null, 40),                           // 无掩码 → 剔除
      sTask(5, '/DiskArray/B', '/DiskArray/B/GF07_mask.tif', 50),   // 他目录 → 剔除
    ];
    const got = tasksForScene(rows, { lqPath: '/DiskArray/A', stem: 'GF07' });
    expect(got.map((t) => t.task_id)).toEqual([2, 1]);
  });

  it('Windows 盘符路径 + 反斜杠分隔同样命中', () => {
    const rows = [
      sTask(7, 'C:\\Disk\\GF07', 'C:\\Disk\\GF07\\GF07_mask.tif', 1),
    ];
    const got = tasksForScene(rows, { lqPath: 'C:\\Disk\\GF07', stem: 'GF07' });
    expect(got.length).toBe(1);
    expect(got[0].task_id).toBe(7);
  });

  it('无匹配 / 空列表 / ref 无 lqPath → 空数组', () => {
    expect(tasksForScene([], { lqPath: '/DiskArray/A', stem: 'GF07' })).toEqual([]);
    expect(tasksForScene([sTask(1, '/DiskArray/A', '/DiskArray/A/GF07_mask.tif', 1)],
                         { lqPath: '/DiskArray/Z', stem: 'GF07' })).toEqual([]);
    expect(tasksForScene([sTask(1, '/DiskArray/A', '/DiskArray/A/GF07_mask.tif', 1)],
                         { lqPath: null, stem: 'GF07' })).toEqual([]);
  });
});

describe('normDir / pathLeafOf（跨平台路径小工具）', () => {
  it('去尾部分隔符；盘符 C: 不受影响', () => {
    expect(normDir('/DiskArray/A')).toBe('/DiskArray/A');
    expect(normDir('/DiskArray/A/')).toBe('/DiskArray/A');
    expect(normDir('C:\\Disk\\GF07\\')).toBe('C:\\Disk\\GF07');
    expect(normDir('C:')).toBe('C:');
  });
  it('取基名，兼容 / 与 \\', () => {
    expect(pathLeafOf('/a/b/GF07_mask.tif')).toBe('GF07_mask.tif');
    expect(pathLeafOf('C:\\run\\x\\GF07_mask.tif')).toBe('GF07_mask.tif');
    expect(pathLeafOf('')).toBe('');
  });
});
