/**
 * queue.test.ts — 队列 store 纯函数（表单归一 / SSE 归并 / 状态徽标）
 * ------------------------------------------------------------------
 * Node 环境：只测纯函数（不触碰 pinia store）。覆盖 api-contract.md §3.3 提交
 * body 归一（mask_path 交后端推导、数值夹取）与 job_update 按 task_id 归并。
 */
import { describe, it, expect } from 'vitest';
import {
  defaultForm, draftToForm, formToSubmit, derivedMaskPath, mergeJobUpdate, stateTone,
  tasksForScene, normDir, pathLeafOf,
  isActiveState, taskElapsed, formatDuration,
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
  it('sr_scale=2 / suffix 留空 / cloud_limit=80 / grid_align=true', () => {
    expect(defaultForm()).toEqual({
      lq_path: '', sr_scale: 2, suffix: '',
      gpu: 0, cloud_limit: 80, delete_ori: false, grid_align: true,
    });
  });

  it('后缀默认留空（交给后端按 SR 配置文件的 <Suffix> 决定）', () => {
    // 前端一旦预填，该值就会作为显式参数压过配置文件 —— 别把它填回去。
    expect(defaultForm().suffix).toBe('');
  });
});

describe('场景目录 → 表单', () => {
  it('只带目录，其余取默认值', () => {
    const d: QueueDraft = { lq_path: '/DiskArray/GF07A03_xxx_L1_PAN' };
    const f = draftToForm(d);
    expect(f.lq_path).toBe(d.lq_path);
    expect(f.sr_scale).toBe(2);
    expect(f.suffix).toBe('');
  });
});

describe('draftToForm（「以这行参数再提交」整组带回）', () => {
  it('tunables 覆盖默认值；delete_ori 不给也恒 false（原型期禁用）', () => {
    const f = draftToForm({
      lq_path: '/DiskArray/A', taskId: 7, from: 'task',
      tunables: { sr_scale: 4, suffix: 'x2', gpu: 1, cloud_limit: 30, grid_align: false },
    });
    expect(f).toEqual({
      lq_path: '/DiskArray/A', sr_scale: 4, suffix: 'x2', gpu: 1,
      cloud_limit: 30, delete_ori: false, grid_align: false,
    });
  });

  it('tunables 只给一部分 → 其余取默认', () => {
    const f = draftToForm({ lq_path: '/a', taskId: 3, from: 'task', tunables: { suffix: 'sr2' } });
    expect(f.suffix).toBe('sr2');
    expect(f.sr_scale).toBe(2);
  });
});

describe('isActiveState / taskElapsed（耗时列）', () => {
  it('未终结态 = SUBMITTING / PENDING / RUNNING', () => {
    for (const s of ['SUBMITTING', 'PENDING', 'RUNNING']) expect(isActiveState(s)).toBe(true);
    for (const s of ['COMPLETED', 'FAILED', 'UNKNOWN', '']) expect(isActiveState(s)).toBe(false);
  });

  it('终态行 = updated_at − created_at（后端每次状态写回都刷 updated_at）', () => {
    const t = task({ state: 'COMPLETED', created_at: 1000, updated_at: 1073 });
    expect(taskElapsed(t, 99999)).toEqual({ seconds: 73, running: false });
  });

  it('运行中的行按 nowSec 现算，不受 updated_at 束缚', () => {
    const t = task({ state: 'RUNNING', created_at: 1000, updated_at: 1000 });
    expect(taskElapsed(t, 1042)).toEqual({ seconds: 42, running: true });
  });

  it('缺 created_at → null（界面显示「—」，不假装 0 秒）', () => {
    expect(taskElapsed(task({ state: 'COMPLETED' }), 100)).toBeNull();
  });

  it('时间戳倒挂 → 夹到 0', () => {
    expect(taskElapsed(task({ state: 'COMPLETED', created_at: 100, updated_at: 50 }), 10))
      .toEqual({ seconds: 0, running: false });
  });
});

describe('formatDuration（耗时文案）', () => {
  it('秒 / 分秒 / 时分', () => {
    expect(formatDuration(0)).toBe('0 秒');
    expect(formatDuration(12)).toBe('12 秒');
    expect(formatDuration(192)).toBe('3 分 12 秒');
    expect(formatDuration(3600)).toBe('1 时 00 分');
    expect(formatDuration(7500)).toBe('2 时 05 分');
  });

  it('负数夹到 0（时钟回拨不显示负耗时）', () => {
    expect(formatDuration(-5)).toBe('0 秒');
  });
});

describe('derivedMaskPath（后端 §4.3 规则的显示镜像）', () => {
  it('同目录 <目录名>_mask.tif', () => {
    expect(derivedMaskPath('/DiskArray/A/B/GF07A03_x'))
      .toBe('/DiskArray/A/B/GF07A03_x/GF07A03_x_mask.tif');
  });

  it('尾部分隔符 / 两侧空白不影响结果', () => {
    expect(derivedMaskPath('  /DiskArray/A/GF07  '))
      .toBe('/DiskArray/A/GF07/GF07_mask.tif');
    expect(derivedMaskPath('/DiskArray/A/GF07/'))
      .toBe('/DiskArray/A/GF07/GF07_mask.tif');
  });

  it('空目录 → 空串（不猜文件名）', () => {
    expect(derivedMaskPath('')).toBe('');
    expect(derivedMaskPath('   ')).toBe('');
  });
});

describe('formToSubmit（提交 body 归一）', () => {
  it('trim + mask_path 恒 null（交后端推导）+ 字段透传', () => {
    const body = formToSubmit({
      lq_path: '  /DiskArray/x  ', sr_scale: 2, suffix: 'sr',
      gpu: 0, cloud_limit: 80, delete_ori: false, grid_align: true,
    });
    expect(body.lq_path).toBe('/DiskArray/x');
    expect(body.mask_path).toBeNull();
    expect(body.suffix).toBe('sr');
    expect(body.grid_align).toBe(true);
    expect(body.delete_ori).toBe(false);
  });

  it('清空后缀 → 空串（交给后端按 SR 配置文件决定，前端不替它决定）', () => {
    const body = formToSubmit({
      lq_path: '/a', sr_scale: 2, suffix: '  ',
      gpu: 0, cloud_limit: 80, delete_ori: false, grid_align: true,
    });
    expect(body.suffix).toBe('');
  });

  it('超界数值夹到合法域（sr_scale 0→1、cloud_limit 150→100）', () => {
    const body = formToSubmit({
      lq_path: '/a', sr_scale: 0, suffix: 't',
      gpu: -2, cloud_limit: 150, delete_ori: true, grid_align: false,
    });
    expect(body.sr_scale).toBe(1);
    expect(body.gpu).toBe(0);
    expect(body.cloud_limit).toBe(100);
    expect(body.suffix).toBe('t');
  });

  it('NaN → 各自默认', () => {
    const body = formToSubmit({
      lq_path: '/a', sr_scale: Number.NaN, suffix: '',
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
