/**
 * lib/qcbatch.ts — 「一键解析」的纯逻辑：产品段候选的镜像 + 批量串行状态机
 * ------------------------------------------------------------------
 * 两件事，都是纯函数（有单测），副作用全在调用方（stores/qclist.ts）：
 *
 * 1. `sceneNameCandidates` 是 **`backend/pathguard.py::scene_name_products` 的镜像**。
 *    镜像**只用于匹配**（把《待修复清单》第一列的名字，对上左侧卡片的场景目录名），
 *    **绝不参与拼路径** —— 拼路径的活只在后端（硬约束：后端按固定候选 stat，前端
 *    不许自己造路径）。所以这里的规则跟后端有一处不同：返回的是**补出来的完整名字**，
 *    不含「补的是哪一段」（那是后端写 404 说明用的，前端用不上）。
 *    两处钉子盯着它别漂：`backend/tests/test_paths.py` 钉 `_PRODUCT_CODES`、
 *    `lib/__tests__/qcbatch.test.ts` 钉同两个字面量。
 *
 * 2. `runSceneBake` 是**注入式**的串行驱动器 —— 三个动作（resolve / 生成本体 / 生成 NOSR）
 *    由调用方注入，循环体自己只负责「按序、跳过失败、计数、每个 await 之后看一次是否
 *    被取消」。注入是为了让这四条规矩能在 Vitest 里用假函数全量测到；真正的 HTTP 与
 *    store 操作留在 stores/qclist.ts 的接线里。
 *
 * 分层：本文件**不 import store**（同 lib/notices.ts 的做法）—— 所以它只吃已经切好的
 * 字符串，路径取末段（`pathLeafOf`）由调用方先做。
 */

/** 产品段的已知取值 —— `backend/pathguard.py::_PRODUCT_CODES` 的镜像。改这里必须
 *  同步改后端那处（两边的测试各钉一次，同时红即不会静默漂）。 */
export const PRODUCT_CODES = ['PAN', 'MSS'] as const;

/** 光栅后缀。拖进来的**文件名**带后缀，而场景目录名从不带 —— 与后端同一条正则。 */
const RASTER_EXT_RE = /\.(?:tiff?|img|jpe?g)$/i;

/** 生产名各段的分隔符：真机是下划线，用户口径里也有空格形态（与后端同）。 */
const FIELD_SEP_RE = /[_\s]+/g;

/**
 * 这个名字该试的「完整生产名」候选（有序）；**原样那条恒排第一**。
 *
 * 清单第一列**约定俗成省掉产品段**（`…_101_0020_001_L1`，盘阵上叫 `…_L1_PAN`），
 * 而产品段是生产名的**最后一段**，按名字反推的两级目录名都拿它拼 —— 少一段就两级
 * 一起错。`hinted` = 那一行写着的影像类型（`pan` / `MSS`，大小写不限）：认得出就把它
 * 排在前面；认不出（`PMS`、`全色`、空串）就按 `PRODUCT_CODES` 的次序（默认 `_PAN`
 * 在先）。补出来的段沿用**原文的分隔符**。
 *
 * 名字自己就带产品段、或带影像后缀时，只回原样一条 —— 与后端逐字同一判据。
 * 空串回空数组。
 */
export function sceneNameCandidates(name: string, hinted?: string | null): string[] {
  const raw = String(name ?? '').trim();
  if (!raw) return [];
  if (RASTER_EXT_RE.test(raw)) return [raw];
  const tokens = raw.split(FIELD_SEP_RE).filter(Boolean);
  if (tokens.length && (PRODUCT_CODES as readonly string[]).includes(tokens[tokens.length - 1].toUpperCase())) {
    return [raw];
  }
  const h = String(hinted ?? '').trim().toUpperCase();
  const order: readonly string[] = (PRODUCT_CODES as readonly string[]).includes(h)
    ? [h, ...PRODUCT_CODES.filter((c) => c !== h)]
    : PRODUCT_CODES;
  const seps = raw.match(FIELD_SEP_RE) ?? [];
  const sep = seps.length ? seps[seps.length - 1] : '_';
  return [raw, ...order.map((c) => raw + sep + c)];
}

/**
 * 清单行 ↔ 左侧卡片**是不是同一景**：卡片场景目录名的末段，落在这一行的候选里即命中。
 *
 * 方向是单向的（目录名 ∈ 候选），因为卡片目录名必然来自后端的 `resolved.dir`，其形态
 * 只能是「裸名」或「裸名 + 分隔符 + 产品段」—— 反过来收候选集合没有额外收益。
 *
 * 命中的代价只有「点一行不亮任何卡」，绝不会亮错一张：候选集合里全是同一个名字的变体。
 */
export function matchesScene(
  rowName: string, hinted: string | null | undefined, dirLeaf: string,
): boolean {
  const leaf = String(dirLeaf ?? '').trim();
  if (!leaf) return false;
  return sceneNameCandidates(rowName, hinted).includes(leaf);
}

/** 进度行文案：`第 3/12 景 · <名字> · ÷4`。 */
export function progressText(i: number, n: number, name: string, div: number): string {
  return '第 ' + i + '/' + n + ' 景 · ' + name + ' · ÷' + div;
}

/** 已用毫秒 → `2 分 10 秒` / `18 秒`（进度行与完成汇总共用）。 */
export function fmtElapsed(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return s + ' 秒';
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r ? m + ' 分 ' + r + ' 秒' : m + ' 分';
}

/** 失败发生在哪一步（决定「第几步」的文案）。 */
export type BakeStage = 'resolve' | 'body' | 'nosr';

export const BAKE_STAGE_LABEL: Record<BakeStage, string> = {
  resolve: '解析场景',
  body: '生成预览本体 jpg',
  nosr: '生成预览 NOSR jpg',
};

export interface BakeFail { stage: BakeStage; reason: string }

export interface BakeReport {
  total: number;
  /** **解析成功、这一景的账已经算上**的行数（含本体生成失败了的那种：那也是一条账）。
   *  解析就没成的、以及取消时还没轮到的，都不计 —— 所以「成功 = done - fails 条数」
   *  报出来的是「左侧真长出卡来的景数」。 */
  done: number;
  stopped: boolean;
  /** 按行名索引：真的失败了（要标红、要记原因）。 */
  fails: Record<string, BakeFail>;
  /** 按行名索引：盘上的事实，不是错误（例如「盘上没有未超分那份」）。 */
  notes: Record<string, string>;
}

/** 驱动器要注入的三个动作 + 一个进度回调。三个动作**失败即抛**，由驱动器记账跳过。 */
export interface BakeDeps<T extends BakeRow = BakeRow> {
  /** 解析一景（拿回后端的权威结果）。失败抛。 */
  resolve(it: T, signal: AbortSignal): Promise<unknown>;
  /** 本体 jpg：入列一张卡 + 生成一份。失败抛。 */
  body(it: T, res: unknown): Promise<void>;
  /** NOSR：问 /siblings，有那份就入列一张卡 + 生成一份；返回 'missing' = 盘上没有。失败抛。 */
  nosr(it: T, res: unknown): Promise<'ok' | 'missing'>;
  onProgress?(i: number, n: number, it: T, phase: BakeStage): void;
}

/** 驱动器只要求行有名字 —— `QcIssue` 结构上满足它（免得 lib 反向 import store 的类型）。
 *  泛型参数顺着这里传下去：调用方注入的闭包拿到的就是它自己的行类型，不必强转。 */
export interface BakeRow { name: string }

/**
 * 按行序**串行**跑完一批：一景走完才开下一景（并发恒为 1）。
 *
 * 为什么必须串行：服务端生成一份 ÷4 的 4 万² 产物峰值内存约 400MB、要把整个文件读一遍，
 * 而后端**没有任何生成预览并发闸门**（`preview` 是 sync def，会进 anyio 线程池）；并发发出去
 * 就是内存 ×N 并把盘阵带宽占满。项目里已有的串行先例是 `viewer.maybePrefetchCompare`。
 *
 * 四条规矩（单测逐条钉住）：
 * 1. **严格按行序**，`rows` 的顺序就是展示顺序；
 * 2. **单景失败不中断整批**：记进 `fails[行名]` 再接着跑下一景；
 * 3. **取消点只看 `signal.aborted`**，且在**每一个 await 之后**都看一次 —— `resolve`
 *    可被 abort，而生成图那两条 HTTP **中断不了**，所以在飞的那一景让它生成完：下一轮循环
 *    开头才发现 abort，`stopped = true`，没轮到的景一个字都不记（不算失败）；
 * 4. `nosr` 返回 `'missing'` 进 `notes` 不进 `fails` —— 盘上没有那份是事实，不是错误。
 *
 * `done` 的加账点定在**解析成功那一刻**，不是循环末尾：这样「本体生成到一半用户按了停止」
 * 也算这一景一笔（那张卡确实已经在左侧列表里了），否则汇总会报「成功 0」而屏幕上明明
 * 摆着卡 —— 那是谎报。反过来，解析就失败/被取消的那些不计，它们一张卡都没落。
 */
export async function runSceneBake<T extends BakeRow>(
  rows: readonly T[], deps: BakeDeps<T>, signal: AbortSignal,
): Promise<BakeReport> {
  const total = rows.length;
  const fails: Record<string, BakeFail> = {};
  const notes: Record<string, string> = {};
  let done = 0;
  let stopped = false;
  // 缩放档位（÷N）由调用方在 deps 闭包里抓成常量、整批沿用，驱动器不碰它 ——
  // 用户跑到一半拖滑块，只影响后面那些景，已生成好的卡文案仍如实标它自己那一档。
  for (let i = 0; i < total; i++) {
    if (signal.aborted) { stopped = true; break; }
    const it = rows[i];
    let res: unknown;
    try {
      deps.onProgress?.(i + 1, total, it, 'resolve');
      res = await deps.resolve(it, signal);
    } catch (e) {
      // 用户按「停止」时，在飞的 `resolve` 会以 AbortError 被拒 —— 那是**取消**，
      // 不是这一景解析失败，不能记进 fails（否则停止键一按就报一堆假失败）。
      if (signal.aborted) { stopped = true; break; }
      fails[it.name] = { stage: 'resolve', reason: errText(e) };
      done += 1;             // 解析失败也是一条账（标红 + 记原因），要计入
      continue;
    }
    done += 1;               // 解析成功：这一景的卡从这里开始算落了地
    if (signal.aborted) { stopped = true; break; }
    deps.onProgress?.(i + 1, total, it, 'body');
    try {
      await deps.body(it, res);
    } catch (e) {
      fails[it.name] = { stage: 'body', reason: errText(e) };
    }
    if (signal.aborted) { stopped = true; break; }
    deps.onProgress?.(i + 1, total, it, 'nosr');
    try {
      if (await deps.nosr(it, res) === 'missing') {
        notes[it.name] = '盘上没有未超分那份（NOSR），只生成了本体';
      }
    } catch (e) {
      // 本体降采样到了、NOSR 没成：**不覆盖**本体那条失败记录（两条记的是不同的事，
      // 而卡片上只有一个名字）。本体已成的状态下这条只进 notes 更准确 —— 但
      // 名字只有一个键，所以本体失败时保留本体那条（更严重、且先发生）。
      if (!fails[it.name]) fails[it.name] = { stage: 'nosr', reason: errText(e) };
      else notes[it.name] = 'NOSR 那份没降采样到：' + errText(e);
    }
  }
  return { total, done, stopped, fails, notes };
}

/** 后端 detail 是中文原话（含「试过哪些候选、各自为什么不行」），原样用。 */
function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
