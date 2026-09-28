/**
 * stores/scenes.ts — 盘阵场景检索/打开（阶段4）
 * ------------------------------------------------------------------
 * 显示 = 服务器预生成 JPG：列表由 GET /api/scenes 提供（后端补 W/H + jpgUrl），
 * 点击打开时若缓存 JPG 未生成先调 GET /api/scenes/{id}/preview 懒生成，再走静态
 * jpgUrl（nginx 直出）；字节交给 viewer store 构造 route='jpg' 的同构 rec。
 *
 * 另一条入口 resolvePath：盘阵上**不在** SR_SCENES_ROOT 之下的场景（生产数据
 * `W:\GSHC2IMPS\PRODUCT\<年>\<月>\<日>\<编号>`）。这类行没有静态 jpgUrl，
 * /preview 的响应体本身就是图，直接拿来用 —— 两条路最终都汇到 view
 * openSceneJpg，rec 结构同构。
 *
 * 运行期 base（apiBase/staticBase）每次从 loadSrConfig() 读取 —— 默认同源（nginx
 * 同时暴露 /api 与 /disk-array）；e2e/异源注入 window.__SR_CFG__。
 */
import { defineStore } from 'pinia';
import { ref, computed, watch } from 'vue';
import { useViewerStore } from './viewer.js';
import type { ClearResponse, SceneRow, SceneQueryParams,
              SceneFilters } from '../lib/scene.js';
import { clearSummaryText, loadSrConfig, parseScenesSnapshot, rowsAfterClear,
         SCENES_SNAPSHOT_KEY, scenesListUrl, scenesSnapshotOf } from '../lib/scene.js';
import { apiClearScenePreviews, apiResolveScene, fetchSceneJpg,
         isSceneGone, isProxyMiss } from '../lib/api.js';
import type { SceneResolveResult } from '../lib/api.js';

export const useScenesStore = defineStore('scenes', () => {
  const rows = ref<SceneRow[]>([]);
  const source = ref<'' | 'disk' | 'fake'>('');
  const scanned = ref(0);
  const count = ref(0);
  const loading = ref(false);
  const openingId = ref<string | null>(null);
  /** 本会话是否已经检索过（`ensureSearched` 的判据）。 */
  const searched = ref(false);
  /** 上一次检索的**发起**时刻；页面那行「上次检索 HH:MM」用它。 */
  const searchedAt = ref<Date | null>(null);
  /** 打开中的阶段文案（首次生成预览很慢，本页没有遮罩，就靠这一行说明在忙什么）。
   *  只在打开期间非空，见 open()。 */
  const phase = ref('');
  const error = ref('');
  const limit = 200;

  // 筛选状态（镜像后端 search_scenes 参数）
  const query = ref('');
  const satellite = ref('');
  const sensor = ref('');
  const dateFrom = ref('');
  const dateTo = ref('');

  /** **出了现在这批行的**筛选条件（list() 成功时定格）。
   *
   *  与上面那几个输入框不是一回事：输入框是「用户正在打的字」，这里是「上次检索用的
   *  条件」。快照要存的是后者 —— 否则用户改完筛选框没按检索就刷新，恢复出来的会是
   *  「一批按旧条件查出来的行 + 一份新条件的输入框」，两边对不上。 */
  const committedFilters = ref<SceneFilters>(
    { query: '', satellite: '', sensor: '', dateFrom: '', dateTo: '' });

  /** 上一次检索**有没有结论**。失败时（catch 那一支）快照必须作废：一份「0 行」的
   *  快照会在刷新后冒充「上次检索」，而那次检索其实是失败的（错误文案不随快照走）。 */
  let listFailed = false;

  const satellites = computed(() => distinct((r) => r.satellite));
  const sensors = computed(() => distinct((r) => r.sensor));
  const dates = computed(() => distinct((r) => r.date));

  function distinct(f: (r: SceneRow) => string | null): string[] {
    const seen = new Set<string>();
    for (const r of rows.value) {
      const v = f(r);
      if (v) seen.add(v);
    }
    return Array.from(seen).sort();
  }

  function params(): SceneQueryParams {
    return {
      query: query.value, satellite: satellite.value || null,
      sensor: sensor.value || null,
      dateFrom: dateFrom.value || null, dateTo: dateTo.value || null,
      limit,
    };
  }

  async function list(): Promise<void> {
    loading.value = true;
    error.value = '';
    // 「已经检索过」在这一刻就成立（不等返回）：失败也算 —— 失败原因已经写在页面上，
    // 再进本页不该自动重来一遍（盘阵上那次全树扫描不便宜），要重来用户按「检索」。
    searched.value = true;
    searchedAt.value = new Date();
    const cfg = loadSrConfig();
    // 这一批行的筛选画像：**发起时就定格**（用户接下来可能在输入框里继续打字，那些字
    // 还没被检索过，不属于这批行）。
    const asked = params();
    const shot: SceneFilters = {
      query: query.value, satellite: satellite.value, sensor: sensor.value,
      dateFrom: dateFrom.value, dateTo: dateTo.value,
    };
    // 新一次检索 = 全新的一批行：旧的选中集与上一次清除结论都作废（否则「已选 3 项」
    // 会指向一批已经不在列表里的 id，下次「清除选定」发出去的就是死 id）。
    clearSelection();
    clearResult.value = null;
    clearRemoved.value = 0;
    clearError.value = '';
    try {
      const resp = await fetch(scenesListUrl(cfg, asked));
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const body = await resp.json() as {
        source: 'disk' | 'fake'; scanned: number; count: number; results: SceneRow[];
      };
      rows.value = body.results;
      source.value = body.source;
      scanned.value = body.scanned;
      count.value = body.count;
      committedFilters.value = shot;
      listFailed = false;
    } catch (e) {
      error.value = '场景检索失败：' + (e instanceof Error ? e.message : String(e));
      // 先记失败标志再清行：watch（见文件尾部）随后会把 sessionStorage 里那份快照
      // 删掉 —— 一次失败的检索不该在刷新后冒充「上次检索」。
      listFailed = true;
      rows.value = [];
    } finally {
      loading.value = false;
    }
  }

  /** 进入场景库页面时调它：**还没检索过才**发一次检索。
   *
   *  以前是每次进入都 `list()`。那条路会把上一轮「清除缓存」的结果当场抹掉 —— 摘掉的
   *  行全回来、结果汇总行消失，用户看到的就是「清除没生效」（清除删的是盘阵上的文件，
   *  那一刻其实没有任何东西被撤销）。现在列表留在页面上，直到用户**自己**按「检索」/
   *  「重置」：与清除那套「摘掉的行要回来得重新检索」（clearSummaryText）同一个口径。
   *
   *  **「还没检索过」包含刷新**：`searched` 由 sessionStorage 里那份快照恢复（见文件
   *  尾部），所以整页重载也不会自动重检索 —— 2026-09-28 用户报的「清除之后每次进页面
   *  那几景又冒出来」正是漏了这一半（守卫纯内存，刷新即复位）。
   *
   *  代价是列表可以是旧的（新落盘的场景、别处刚生成的的预览都不反映），所以状态 chip
   *  旁边带一行「上次检索 HH:MM」，让用户看得出这份数据是什么时候的。单个 `.jpg`/`.tif`
   *  的标签由取图那一侧就地翻牌（lib/api.ts 的 fetchSceneJpg 写回本行），不靠重检索。 */
  function ensureSearched(): void {
    if (!searched.value) void list();
  }

  function resetFilters(): void {
    query.value = ''; satellite.value = ''; sensor.value = '';
    dateFrom.value = ''; dateTo.value = '';
    void list();
  }

  /* ---------------- 清除预览缓存（勾选 + 清除选定 / 全部清除） ----------------
   * 服务端把盘阵上那一景的 `<stem>_preview.jpg` 删掉（判据在
   * backend/services/preview_clear.py）。本 store 只管三件事：谁被选中、发请求、
   * 按结论决定哪些行留在列表里。 */

  /** 选中态按 id 记（不是按行下标）：清除后行会从 rows 里摘掉，下标全变。 */
  const selected = ref<Set<string>>(new Set());
  const clearing = ref(false);
  /** 上一次清除的完整结论（null = 还没清过）。明细展不展开由组件决定。 */
  const clearResult = ref<ClearResponse | null>(null);
  /** 上一次清除摘掉了几行 —— 汇总文案里那句「已从列表移除 N 行」用它。 */
  const clearRemoved = ref(0);
  const clearError = ref('');

  /** 能清的行 = 非 fake。fake 行在盘上没有文件（未配 SR_SCENES_ROOT 的占位），
   *  发过去只会换来一条「场景不可访问」，所以连勾选框都不给。 */
  function isSelectable(row: SceneRow): boolean {
    return !row.fake;
  }

  const selectableRows = computed(() => rows.value.filter(isSelectable));
  const selectedCount = computed(() => selected.value.size);
  const allSelected = computed(() =>
    selectableRows.value.length > 0
    && selectableRows.value.every((r) => selected.value.has(r.id)));

  function toggleSelect(id: string): void {
    const s = selected.value;
    if (s.has(id)) s.delete(id);
    else s.add(id);
  }

  function setAllSelected(flag: boolean): void {
    selected.value = flag
      ? new Set(selectableRows.value.map((r) => r.id))
      : new Set();
  }

  function clearSelection(): void {
    selected.value = new Set();
  }

  /** 清除一组 id 的预览缓存，按结论摘行。
   *
   *  `cleared` / `nothing` 的行摘掉（盘上的事实是「没有缓存」，留着就是继续显示
   *  「已生成」这个谎），`skipped` / `failed` 的留下 —— 用户要看得见失败和原因。
   *  **不重新检索**：这次不动的行照旧待在列表里，用户看得到自己刚做了什么；
   *  摘掉的行要回来得重新检索（汇总文案里明说了）。 */
  async function runClear(ids: string[]): Promise<void> {
    if (clearing.value || !ids.length) return;
    clearing.value = true;
    clearError.value = '';
    try {
      const body = await apiClearScenePreviews(loadSrConfig(), ids);
      clearResult.value = body;
      const before = rows.value.length;
      rows.value = rowsAfterClear(rows.value, body.results);
      clearRemoved.value = before - rows.value.length;
      // 表头 chip 上的「命中 N」得跟着动，否则与可见行数对不上
      count.value = Math.max(0, count.value - clearRemoved.value);
      // 摘掉的行不该还留在选中集里（否则下次「清除选定」会发一批已经不存在的 id）
      const left = new Set(rows.value.map((r) => r.id));
      selected.value = new Set([...selected.value].filter((id) => left.has(id)));
    } catch (e) {
      clearError.value = '清除缓存失败：'
        + (e instanceof Error ? e.message : String(e));
      clearResult.value = null;
      clearRemoved.value = 0;
    } finally {
      clearing.value = false;
    }
  }

  /** 清除选定：只清勾上的那些行。 */
  async function clearSelected(): Promise<void> {
    await runClear([...selected.value]);
  }

  /** 全部清除：**当前检索结果**（受筛选影响，就是列表里那些行）。
   *
   *  不含 fake 行；`limit` 截断时超出列表的部分也清不到 —— 这两件事都由
   *  SceneCacheBar 在确认文案里写明，用户不必猜自己清了多大范围。 */
  async function clearAllRows(): Promise<void> {
    await runClear(selectableRows.value.map((r) => r.id));
  }

  /** 汇总一行文案（纯函数在 lib/scene.ts，便于单测）。 */
  const clearSummary = computed(() => clearResult.value
    ? clearSummaryText(clearResult.value.summary, clearRemoved.value) : '');

  /** 打开场景：确保 JPG 已生成 → 静态 jpgUrl 读字节 → viewer.openSceneJpg。
      失败一律写本 store 的 error —— 本页（ScenesPage）只渲染 scenes.error，而
      viewer 的错误条挂在 /viewer、6 秒后自己消失；写错地方就等于按钮点了没反应。
      JPG 读不到 / 字节不是图（openSceneJpg 解码失败会抛，见 viewer.ts）都归这里。

      其中「盘阵上已经没有这个文件」（**静态链**上的 404）多一步：把这一行标成
      `purged`，页面上那格的「打开」换成不可点的「已自动清除」（见 catch 里的注释）。
      **别的 404 不在这一支** —— 打后端的请求回 404 说明不了文件在不在，如实报出来
      （见 api.ts 的 isProxyMiss）。

      反过来，**打开成功**也是一条实证：那一景还在盘上（而且这趟之后盘上有了它当前
      档位的预览）。所以成功路径同时把列表里同一景的那一行翻回来，见 try 末尾。 */
  async function open(row: SceneRow, resolved?: SceneResolveResult['resolved']): Promise<void> {
    const viewer = useViewerStore();
    const cfg = loadSrConfig();
    if (row.fake) {
      error.value = '「' + row.name + '」是 fake 占位场景'
        + '（未配 SR_SCENES_ROOT 或盘阵不可达），没有真实文件可打开';
      return;
    }
    if (!row.W || !row.H) {
      error.value = '「' + row.name + '」尺寸未知（元数据缺 W/H），无法换算掩码，拒绝打开';
      return;
    }
    openingId.value = row.id;
    error.value = '';
    try {
      // 首次要服务端生成（读一遍大图，几十秒）。遮罩（viewer.showMask）只挂在
      // /viewer 上，本页调了也看不见，所以这里走自己的 phase 文案行。
      // 档位取 viewer store 里的那份（工具栏拖动条写的就是它）—— 这样用户拖完
      // 滑块不必刷新页面就生效；loadPreviewDiv() 只负责冷启动时的初值。
      const blob = await fetchSceneJpg(cfg, row, viewer.previewDiv,
                                       (text) => { phase.value = text; });
      await viewer.openSceneJpg({
        name: row.name, W: row.W, H: row.H, sceneId: row.id,
        // 无伴随件的 .tif 且父目录不是场景目录时后端把它置 null（= 不能提交 SR），
        // 这里不用再判 sr_capable —— 与 resolved.sr_capable 同源同真假。
        lqPath: row.lq_path,
        // 场景目录（同上，与能不能提交无关）：卡片上那颗「同一景共用一个序号」
        // 的小标按它分组，场景库开出来的行同样该有号。
        sceneDir: row.lq_path,
        // 手工场景（resolvePath 进来的）：把后端推导的掩码路径一并带上，
        // 否则这条入口的 rec 少一个 serverMaskPath，「保存掩码到盘阵」前后
        // 显示的掩码路径与查看器那条入口不一致（两边最终都以后端回的为准）。
        serverMaskPath: resolved ? resolved.mask_path : null,
      }, blob);
      // 打开成功 = 这一景**确实还在盘上**的实证，而且这一趟之后盘上必有它当前档位的
      // 预览（fetchSceneJpg 的保证）。若当前列表里也有它，就地翻回来 —— 那一格的灰块
      // 「已自动清除」是按**推定**画的，别让列表继续印着一个刚落空的结论。
      // 手工入口（路径栏 / 按名打开）拿到的是另一份行对象，只能按场景目录对回去。
      const listed = rows.value.find((r) => r !== row && !!r.lq_path
        && r.lq_path === row.lq_path);
      if (listed) {
        listed.hasPreview = true;
        listed.previewDiv = viewer.previewDiv;
        listed.purged = false;
      }
    } catch (e) {
      if (isSceneGone(e)) {
        // 盘阵**静态链**上已经没有这个文件了（打开撞的 404，判据见 api.ts 的
        // isSceneGone）。列表是「上次检索」那一刻的快照，盘阵上的数据却会被自动清理
        // —— 于是行还在、文件没了。撞一次就记在这行上，那一格的「打开」就此作废
        // （灰色「已自动清除」，见 SceneRow.purged）：再点一次还是同一个 404，留着一颗
        // 能点的按钮只会让人反复撞墙。**只陈述「取不到」，不写死原因** —— 自动清理是
        // 最像的那个（用户的口径：产出后几天），但平台没在盘上看到过清理这件事。
        row.purged = true;
        error.value = '打开「' + row.name + '」失败：盘阵上已没有这个文件（HTTP 404）。'
          + '这一行的「打开」已改成「已自动清除」；'
          + '列表可能是清理之前的旧结果，按「检索」刷新即知它还在不在。';
      } else if (isProxyMiss(e)) {
        // 打后端的请求**没走到后端**（响应不是 JSON）。**不置 purged**：这个状态码
        // 来自别人，说明不了盘上有没有这一景 —— 2026-09-23 那次「老景打不开」正是
        // 这一支（nginx 在 /api/ 下漏了同一层的 proxy_pass），当时只看 404 就把能打开
        // 的行标成了「已自动清除」。这里如实说清是配置问题并给出自查方向。
        error.value = '打开「' + row.name + '」失败：'
          + (e instanceof Error ? e.message : String(e))
          + '，且响应不是后端给的 JSON —— 这次请求没走到平台后端（多半是 nginx 反代'
          + '配置：/api/ 下的 location 少了同一层的 proxy_pass，见 deploy/nginx.conf）。'
          + '这个状态码说明不了盘阵上还有没有这一景。';
      } else {
        error.value = '打开「' + row.name + '」失败：'
          + (e instanceof Error ? e.message : String(e));
      }
    } finally {
      phase.value = '';
      openingId.value = null;
    }
  }

  /** 手工打开盘阵上的任意一个合法场景目录（`W:\GSHC2IMPS\PRODUCT\...\<编号>`
      或 `/DiskArray/...`）。

      **不扫盘**：只把用户给的这一个路径交给后端 stat（POST /api/scenes/resolve）。
      找不到就报错 —— 后端的 detail 里带着试过哪些候选、各自为什么不行，原样
      呈现给用户让他手填。成功才 open(row)，失败什么都不留下（没有可提交的
      东西），绝不静默换一条路径。返回是否成功。 */
  async function resolvePath(path: string): Promise<boolean> {
    const trimmed = String(path ?? '').trim();
    error.value = '';
    if (!trimmed) {
      error.value = '请填写场景目录路径';
      return false;
    }
    try {
      const res = await apiResolveScene(loadSrConfig(), { path: trimmed });
      await open(res.row, res.resolved);
      return !error.value;
    } catch (e) {
      error.value = '打开失败：' + (e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      openingId.value = null;
    }
  }

  /** 按**生产全名**打开一个盘阵场景（查看器「待修复清单」面板的行内「打开」用）。
   *
   *  与 resolvePath 的差别只在请求体：那条给 `{path}`，这条给 `{name}` —— 日期由
   *  后端从名字里的成像时间戳自己取，所以清单里那串产品名（含时间戳）直接就能用。
   *
   *  `product`（可选）= 那一行写着的**影像类型**（`it.imgType`：`PAN` / `pan` / `MSS`）。
   *  清单第一列的名字**约定俗成省掉产品段**（`…_101_0020_001_L1`，而盘阵上的景级目录叫
   *  `…_101_0020_001_L1_PAN`），少了它后端连段级目录名一起拼错、**整批图都打不开**
   *  （2026-09-27 用户报的 bug）。补哪一段由后端按这个提示决定，没给/认不出就 `_PAN`
   *  在先（用户口径「默认按照 _PAN 打开即可」）—— 前端不自拼名字，只把清单上的事实
   *  递过去。
   *
   *  返回值是**错误串而不是布尔**：本函数的调用方在 /viewer，而 `open()` 按老规矩
   *  把失败写进 `scenes.error`，那个 ref 只在 ScenesPage 渲染 —— 在查看器上写它
   *  等于没写，用户只会看到「点了没反应」。所以由调用方拿去喂 viewer.showErr。
   *  返回 '' 表示成功。 */
  async function openByName(name: string, product?: string): Promise<string> {
    const trimmed = String(name ?? '').trim();
    if (!trimmed) return '名字为空';
    const prod = String(product ?? '').trim();
    error.value = '';
    try {
      const res = await apiResolveScene(loadSrConfig(),
        { name: trimmed, ...(prod ? { product: prod } : {}) });
      await open(res.row, res.resolved);
    } catch (e) {
      return '打开「' + trimmed + '」失败：' + (e instanceof Error ? e.message : String(e));
    }
    // open() 内部也会失败（fake 行 / 缺 W,H / 拉 JPG 出错），原因已经写在 error 里，
    // 原样带出去 —— 后端 resolve 的 detail 里有「试过哪些候选、各自为什么不行」。
    // **一律以 error 为准**：open() 的每条失败路都会写它（两个早退分支 + catch），
    // 所以这里为空 == 真的开出来了。早先这里还 `|| '打开「…」失败'` 兜了底，结果是
    // **成功也返回错误串** —— 面板拿到非空就弹错误条，「打开」点下去图开了、红条
    // 却说失败（2026-09-27 与产品段那个 bug 一起发现的）。
    return error.value;
  }

  /** 按名字**只解析、不打开**：拿回后端的权威结果给「一键解析」批量用。
   *
   *  与 openByName 的差别只有一个：这条路**不 open()** —— 不激活、不弹遮罩、不生成图、
   *  不写 `scenes.error`（批量几十景，一路弹遮罩等于把界面锁死；错误由批量那边记账）。
   *  请求体口径与 openByName 逐字相同（`product` 只在非空时带）。
   *
   *  失败**抛**（不是返回错误串）：批量的调用方要的是「这一景为什么不行」这句话本身，
   *  后端 detail 里带着试过哪些候选、各自为什么不行，原样进失败账。
   *
   *  不复用 openByName：那条路是 2026-09-27 两个 bug 的落点（「成功也返回错误串」），
   *  为了共用几行请求体去动它，风险大于这几行的重复。 */
  async function resolveByName(
    name: string, product?: string, opts?: { signal?: AbortSignal },
  ): Promise<SceneResolveResult> {
    const trimmed = String(name ?? '').trim();
    if (!trimmed) throw new Error('名字为空');
    const prod = String(product ?? '').trim();
    return await apiResolveScene(loadSrConfig(),
      { name: trimmed, ...(prod ? { product: prod } : {}) }, { signal: opts?.signal });
  }

  /* ---------------- 快照：让「上次检索」活过整页重载 ----------------
   * 形状、键名、判据与「为什么是 sessionStorage」全在 lib/scene.ts 那一节。这里只管
   * 三件事：什么时候写、什么时候删、建店时怎么读回来。 */

  /** 写快照。任何写不进去的情况都不算错 —— 最坏只是刷新后退回「进页自动检索」。 */
  function saveSnapshot(): void {
    try {
      if (typeof sessionStorage === 'undefined') return;
      if (listFailed) { sessionStorage.removeItem(SCENES_SNAPSHOT_KEY); return; }
      const snap = scenesSnapshotOf({
        at: searchedAt.value, source: source.value, scanned: scanned.value,
        count: count.value, rows: rows.value, filters: committedFilters.value,
        clearRemoved: clearRemoved.value, clearResult: clearResult.value,
      });
      // 没检索过（source 为空）→ 没有快照可存，同时把上一份删掉，免得它冒充新的。
      if (!snap) { sessionStorage.removeItem(SCENES_SNAPSHOT_KEY); return; }
      sessionStorage.setItem(SCENES_SNAPSHOT_KEY, JSON.stringify(snap));
    } catch { /* 配额满 / 隐私模式 / sessionStorage 不可用：放弃快照，不影响本次会话 */ }
  }

  /** 建店时读回快照。**成功即 `searched = true`** —— 这是这一整块的全部意义：
   *  ScenesPage.onMounted 的 ensureSearched 不再发检索，上一轮的清除结论（摘掉的行、
   *  汇总行）也就不会被一次自动检索当场抹掉。 */
  function restoreSnapshot(): void {
    try {
      if (typeof sessionStorage === 'undefined') return;
      const snap = parseScenesSnapshot(sessionStorage.getItem(SCENES_SNAPSHOT_KEY));
      if (!snap) return;
      rows.value = snap.rows;
      source.value = snap.source;
      scanned.value = snap.scanned;
      count.value = snap.count;
      searchedAt.value = new Date(snap.at);
      committedFilters.value = { ...snap.filters };
      // 输入框也跟着回到那次检索的条件：否则恢复出来的行是「一批按筛选查出来的」，
      // 而框里空着，用户没法解释为什么只有这几条。
      query.value = snap.filters.query;
      satellite.value = snap.filters.satellite;
      sensor.value = snap.filters.sensor;
      dateFrom.value = snap.filters.dateFrom;
      dateTo.value = snap.filters.dateTo;
      clearRemoved.value = snap.clearRemoved;
      clearResult.value = snap.clearResult;
      // 选中态**不恢复**（见 lib/scene.ts 那一节）：刷新后还勾着几行，下一次
      // 「清除选定」就是在清一批用户没看见勾选过程的 id。
      selected.value = new Set();
      searched.value = true;
    } catch { /* 读不出 / 写坏：当没有快照，照旧自动检索 */ }
  }

  /** 上面七个量里任何一个变了就落一次快照。
   *
   *  用 watch 而不是在每个改动点手写一句：改这些量的地方有五六处（检索成功 / 失败、
   *  清除成功、打开成功后按场景目录翻牌、撞 404 标 purged、用户「知道了」收起汇总），
   *  漏一处就是一类「刷新后又对不上」。
   *
   *  `deep` 是必要的：打开成功那条路是**就地改行里的字段**（row.hasPreview / purged），
   *  数组引用没变。行数最多 limit（200）条，深度遍历的成本可以忽略。 */
  watch(
    [rows, source, scanned, count, searchedAt, committedFilters,
     clearRemoved, clearResult],
    saveSnapshot,
    { deep: true },
  );

  restoreSnapshot();

  return {
    rows, source, scanned, count, loading, openingId, phase, error,
    searched, searchedAt,
    query, satellite, sensor, dateFrom, dateTo,
    satellites, sensors, dates,
    list, ensureSearched, resetFilters, open, resolvePath, openByName, resolveByName,
    // 清除预览缓存（SceneCacheBar 用）
    selected, clearing, clearResult, clearRemoved, clearError, clearSummary,
    selectableRows, selectedCount, allSelected,
    isSelectable, toggleSelect, setAllSelected, clearSelection,
    clearSelected, clearAllRows,
  };
});
