/**
 * qcbatch.test.ts — 「一键解析」纯逻辑（产品段候选镜像 + 批量串行状态机）
 * ------------------------------------------------------------------
 * 两类断言，各盯一件事：
 *
 * 1. `sceneNameCandidates` 是 `backend/pathguard.py::scene_name_products` 的**镜像**，
 *    所以这里逐字断言返回数组（它是契约，不是实现细节）。它与 `PRODUCT_CODES` 一起
 *    构成跨语言契约的**前端那一半钉子** —— 后端那一半在
 *    `backend/tests/test_paths.py::TestSceneNameProducts::test_product_codes_mirror`。两处同时红 = 有人
 *    改了产品段口径却没改另一头，这正是要的效果。
 *
 * 2. `runSceneBake` 是**注入式**驱动器，好处就在这里：三个动作全是假函数，于是
 *    「按序 / 跳过失败 / 计数 / 取消点」四条规矩能在几毫秒内全量测到，不必真去读盘。
 *    真机那条链路的代价（每景几十秒）没法进单测，所以状态机必须自己证明自己。
 */
import { describe, it, expect } from 'vitest';
import {
  PRODUCT_CODES, sceneNameCandidates, matchesScene,
  progressText, fmtElapsed, runSceneBake,
} from '../qcbatch.js';
import type { BakeDeps, BakeRow, BakeStage } from '../qcbatch.js';

/* 真机形态的裸名（清单第一列的常态：省掉产品段） */
const BARE = 'JXGF07D03_PMS_20260622052600_200516571_101_0020_001_L1';
/* 盘阵上的景级目录名（= 上面那个 + 产品段） */
const PAN = BARE + '_PAN';

describe('PRODUCT_CODES —— 跨语言契约的前端那一半钉子', () => {
  it('与 backend/pathguard.py::_PRODUCT_CODES 逐字相同', () => {
    // 改这里必须同改后端那处；两边各有一条测试盯着这个词面量，同时红即不会静默漂。
    expect([...PRODUCT_CODES]).toEqual(['PAN', 'MSS']);
  });
});

describe('sceneNameCandidates —— 后端 scene_name_products 的镜像', () => {
  it('裸名 + pan → 原样在前，补出来的按提示优先', () => {
    expect(sceneNameCandidates(BARE, 'pan')).toEqual([BARE, BARE + '_PAN', BARE + '_MSS']);
  });

  it('hinted 大小写不限，且只影响顺序不影响集合', () => {
    expect(sceneNameCandidates(BARE, 'MSS')).toEqual([BARE, BARE + '_MSS', BARE + '_PAN']);
    expect(sceneNameCandidates(BARE, 'mss')).toEqual([BARE, BARE + '_MSS', BARE + '_PAN']);
  });

  it('认不出的 hinted（PMS / 全色 / 空串 / 缺省）→ 按 _PRODUCT_CODES 次序，_PAN 在先', () => {
    const want = [BARE, BARE + '_PAN', BARE + '_MSS'];
    expect(sceneNameCandidates(BARE, 'PMS')).toEqual(want);
    expect(sceneNameCandidates(BARE, '全色')).toEqual(want);
    expect(sceneNameCandidates(BARE, '')).toEqual(want);
    expect(sceneNameCandidates(BARE)).toEqual(want);
    expect(sceneNameCandidates(BARE, null)).toEqual(want);
  });

  it('名字自己就带产品段 → 只回原样一条（真机形态一个候选都不多花）', () => {
    expect(sceneNameCandidates(PAN, 'pan')).toEqual([PAN]);
    expect(sceneNameCandidates(BARE + '_mss')).toEqual([BARE + '_mss']);
  });

  it('带影像后缀的名字（拖进来的文件名）一条都不补', () => {
    // 后端 404 说明里那句「四不像」的由来：补出来的 `…_preview.jpg_PAN` 盘阵上从没有。
    expect(sceneNameCandidates('A_001_L1.tif')).toEqual(['A_001_L1.tif']);
    expect(sceneNameCandidates('A_001_L1.TIFF', 'pan')).toEqual(['A_001_L1.TIFF']);
    expect(sceneNameCandidates('A_001_L1.jpeg')).toEqual(['A_001_L1.jpeg']);
    expect(sceneNameCandidates('A_001_L1.img')).toEqual(['A_001_L1.img']);
  });

  it('补出来的段沿用原文的分隔符（空格形态只有用户口径里有）', () => {
    const spaced = 'JXGF07D03 PMS 20260622052600 200516571 101 0020 001 L1';
    expect(sceneNameCandidates(spaced, 'MSS')).toEqual([
      spaced, spaced + ' MSS', spaced + ' PAN',
    ]);
  });

  it('混用分隔符时取**紧挨末段**的那一个（与后端 seps[-1] 同源）', () => {
    // 分隔符列表是 ['_', ' ', ' ']，末段前任然是空格 → 补出来的段也用空格
    expect(sceneNameCandidates('A_B 001 L1', 'pan')).toEqual([
      'A_B 001 L1', 'A_B 001 L1 PAN', 'A_B 001 L1 MSS',
    ]);
    // 末段前是下划线（哪怕前面有空格）→ 用下划线
    expect(sceneNameCandidates('A B 001_L1', 'pan')).toEqual([
      'A B 001_L1', 'A B 001_L1_PAN', 'A B 001_L1_MSS',
    ]);
  });

  it('空串 / 纯空白 → 空数组（没有可试的候选，调用方不必再判）', () => {
    expect(sceneNameCandidates('')).toEqual([]);
    expect(sceneNameCandidates('   ')).toEqual([]);
    expect(sceneNameCandidates(null as unknown as string)).toEqual([]);
  });

  it('不 trim 结果里的原文段：前后空白只用于判断，补出来的段接在 trim 过的名字后', () => {
    expect(sceneNameCandidates('  ' + BARE + '  ', 'pan')).toEqual([
      BARE, BARE + '_PAN', BARE + '_MSS',
    ]);
  });
});

describe('matchesScene —— 清单行 ↔ 卡片场景目录名', () => {
  it('清单缺产品段、卡片带产品段：命中（2026-09-27 那个缺口的判据）', () => {
    expect(matchesScene(BARE, 'pan', PAN)).toBe(true);
    expect(matchesScene(BARE, 'MSS', PAN)).toBe(true);   // 顺序不影响集合
  });

  it('行名与目录名逐字相同：也命中（原样那条恒在候选里）', () => {
    expect(matchesScene(PAN, 'pan', PAN)).toBe(true);
    expect(matchesScene(BARE, 'pan', BARE)).toBe(true);
  });

  it('不是同一景 / 空末段：不命中（最坏是没点亮，绝不会点亮错的）', () => {
    expect(matchesScene(BARE, 'pan', BARE + '_L2')).toBe(false);
    expect(matchesScene(BARE, 'pan', '')).toBe(false);
    expect(matchesScene(BARE, 'pan', '   ')).toBe(false);
    expect(matchesScene('', 'pan', PAN)).toBe(false);
    expect(matchesScene(BARE, 'pan', null as unknown as string)).toBe(false);
  });

  it('只认方向「目录名 ∈ 候选」，不做反向包含（候选里没有的形态一律不亮）', () => {
    // 「本体的 stem 再加一段」这种盘阵上不存在的名字不该命中 —— 否则一个前缀像的
    // 场景目录会被误点亮。
    expect(matchesScene(BARE, 'pan', PAN + '_DEBUG')).toBe(false);
  });
});

describe('progressText / fmtElapsed —— 进度行文案', () => {
  it('进度行：第 i/n 景 · 名字 · ÷N', () => {
    expect(progressText(3, 12, PAN, 4)).toBe('第 3/12 景 · ' + PAN + ' · ÷4');
  });

  it('已用时长：不足一分钟报秒，够了报分（整分不拖一个「0 秒」）', () => {
    expect(fmtElapsed(0)).toBe('0 秒');
    expect(fmtElapsed(18_400)).toBe('18 秒');
    expect(fmtElapsed(59_400)).toBe('59 秒');
    expect(fmtElapsed(59_600)).toBe('1 分');        // 四舍五入到 60 就换到分那一档
    expect(fmtElapsed(60_000)).toBe('1 分');
    expect(fmtElapsed(130_000)).toBe('2 分 10 秒');
    expect(fmtElapsed(-5)).toBe('0 秒');            // 时钟回拨/负值不报一个负数出来
  });
});

/* ---------------- 批量串行状态机 ---------------- */

/** 假 deps：三个动作只记调用序，行为由各条用例覆盖。 */
function harness(over: Partial<BakeDeps> = {}) {
  const calls: string[] = [];
  const deps: BakeDeps = {
    resolve: async (it) => { calls.push('resolve:' + it.name); return { name: it.name }; },
    body: async (it) => { calls.push('body:' + it.name); },
    nosr: async (it) => { calls.push('nosr:' + it.name); return 'ok'; },
    ...over,
  };
  return { calls, deps };
}

const ROWS: BakeRow[] = [{ name: 'a' }, { name: 'b' }, { name: 'c' }];
const never = new AbortController();

describe('runSceneBake —— 严格按行序、串行', () => {
  it('一景三步走完才开下一景，顺序就是 rows 的顺序', async () => {
    const { calls, deps } = harness();
    const r = await runSceneBake(ROWS, deps, never.signal);
    expect(calls).toEqual([
      'resolve:a', 'body:a', 'nosr:a',
      'resolve:b', 'body:b', 'nosr:b',
      'resolve:c', 'body:c', 'nosr:c',
    ]);
    expect(r).toEqual({ total: 3, done: 3, stopped: false, fails: {}, notes: {} });
  });

  it('onProgress 每一步都报一次，档位由调用方抓（驱动器不碰档位）', async () => {
    const seen: string[] = [];
    const { deps } = harness({
      onProgress: (i, n, it, phase: BakeStage) => { seen.push(`${i}/${n} ${it.name} ${phase}`); },
    });
    await runSceneBake([ROWS[0]], deps, never.signal);
    expect(seen).toEqual(['1/1 a resolve', '1/1 a body', '1/1 a nosr']);
  });

  it('空清单：一个动作都不跑', async () => {
    const { calls, deps } = harness();
    const r = await runSceneBake([], deps, never.signal);
    expect(calls).toEqual([]);
    expect(r.done).toBe(0);
  });
});

describe('runSceneBake —— 单景失败不中断整批', () => {
  it('第 2 景解析失败 → 记 resolve 失败，第 3 景照跑', async () => {
    const { calls, deps } = harness({
      resolve: async (it) => {
        calls.push('resolve:' + it.name);
        if (it.name === 'b') throw new Error('盘阵上没有这一景（试过 3 个候选）');
        return {};
      },
    });
    const r = await runSceneBake(ROWS, deps, never.signal);
    expect(r.fails).toEqual({ b: { stage: 'resolve', reason: '盘阵上没有这一景（试过 3 个候选）' } });
    expect(r.notes).toEqual({});
    expect(r.done).toBe(3);
    expect(calls).toEqual([
      'resolve:a', 'body:a', 'nosr:a',
      'resolve:b',
      'resolve:c', 'body:c', 'nosr:c',
    ]);
  });

  it('本体烤失败 → 同一景的 NOSR 仍然继续（两份是独立的两步）', async () => {
    const { calls, deps } = harness({
      body: async (it) => {
        calls.push('body:' + it.name);
        if (it.name === 'a') throw new Error('烘焙超时');
      },
    });
    const r = await runSceneBake([ROWS[0]], deps, never.signal);
    expect(calls).toEqual(['resolve:a', 'body:a', 'nosr:a']);
    expect(r.fails).toEqual({ a: { stage: 'body', reason: '烘焙超时' } });
  });

  it('NOSR 自己失败（本体成功）→ 记 nosr 失败', async () => {
    const { deps } = harness({
      nosr: async () => { throw new Error('siblings 502'); },
    });
    const r = await runSceneBake([ROWS[0]], deps, never.signal);
    expect(r.fails).toEqual({ a: { stage: 'nosr', reason: 'siblings 502' } });
    expect(r.notes).toEqual({});
  });

  it('本体已失败、NOSR 也失败 → 保留本体那条（更严重且先发生），NOSR 落进 notes', async () => {
    const { deps } = harness({
      body: async () => { throw new Error('烘焙超时'); },
      nosr: async () => { throw new Error('siblings 502'); },
    });
    const r = await runSceneBake([ROWS[0]], deps, never.signal);
    expect(r.fails).toEqual({ a: { stage: 'body', reason: '烘焙超时' } });
    expect(r.notes).toEqual({ a: 'NOSR 那份没烤成：siblings 502' });
  });

  it("NOSR 返回 'missing' → 进 notes 不进 fails（盘上没有那份是事实）", async () => {
    const { deps } = harness({ nosr: async () => 'missing' });
    const r = await runSceneBake([ROWS[0]], deps, never.signal);
    expect(r.fails).toEqual({});
    expect(r.notes).toEqual({ a: '盘上没有未超分那份（NOSR），只烤了本体' });
    expect(r.done).toBe(1);
  });

  it('失败原因非 Error（字符串/对象）也如实记，不写成 [object Object]', async () => {
    const { deps } = harness({ body: async () => { throw '裸字符串原因'; } });
    const r = await runSceneBake([ROWS[0]], deps, never.signal);
    expect(r.fails.a.reason).toBe('裸字符串原因');
  });
});

describe('runSceneBake —— 取消点', () => {
  it('resolve 里被 abort：算停止、**不算这一景失败**（停止键一按就报一堆假失败是 bug）', async () => {
    const ac = new AbortController();
    const { calls, deps } = harness({
      resolve: async (it) => {
        calls.push('resolve:' + it.name);
        ac.abort();
        throw new DOMException('Aborted', 'AbortError');
      },
    });
    const r = await runSceneBake(ROWS, deps, ac.signal);
    expect(r.stopped).toBe(true);
    expect(r.fails).toEqual({});
    expect(r.notes).toEqual({});
    expect(r.done).toBe(0);            // 这一景没走完，不该计数
    expect(calls).toEqual(['resolve:a']);
  });

  it('第一景跑完、开第二景之前被 abort：第二景一步都不跑，且不算失败', async () => {
    const ac = new AbortController();
    const { calls, deps } = harness({
      nosr: async (it) => {
        calls.push('nosr:' + it.name);
        if (it.name === 'a') ac.abort();
        return 'ok';
      },
    });
    const r = await runSceneBake(ROWS, deps, ac.signal);
    expect(calls).toEqual(['resolve:a', 'body:a', 'nosr:a']);
    expect(r.stopped).toBe(true);
    expect(r.done).toBe(1);
    expect(r.fails).toEqual({});
  });

  it('本体那步中途被 abort：NOSR 不再发，也不算失败（在烤的那一份停不下来，就是让它烤完）', async () => {
    const ac = new AbortController();
    const { calls, deps } = harness({
      body: async (it) => { calls.push('body:' + it.name); ac.abort(); },
    });
    const r = await runSceneBake(ROWS, deps, ac.signal);
    expect(calls).toEqual(['resolve:a', 'body:a']);
    expect(r.stopped).toBe(true);
    expect(r.done).toBe(1);             // 本体烤成了，这张卡留着
    expect(r.fails).toEqual({});
  });

  it('一开始就是 aborted：一个动作都不跑', async () => {
    const ac = new AbortController();
    ac.abort();
    const { calls, deps } = harness();
    const r = await runSceneBake(ROWS, deps, ac.signal);
    expect(calls).toEqual([]);
    expect(r).toEqual({ total: 3, done: 0, stopped: true, fails: {}, notes: {} });
  });
});
