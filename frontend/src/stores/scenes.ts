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
import { ref, computed } from 'vue';
import { useViewerStore } from './viewer.js';
import type { SceneRow, SceneQueryParams } from '../lib/scene.js';
import { loadSrConfig, scenesListUrl } from '../lib/scene.js';
import { apiResolveScene, fetchSceneJpg } from '../lib/api.js';
import type { SceneResolveResult } from '../lib/api.js';

export const useScenesStore = defineStore('scenes', () => {
  const rows = ref<SceneRow[]>([]);
  const source = ref<'' | 'disk' | 'fake'>('');
  const scanned = ref(0);
  const count = ref(0);
  const loading = ref(false);
  const openingId = ref<string | null>(null);
  /** 打开中的阶段文案（首次烘焙很慢，本页没有遮罩，就靠这一行说明在忙什么）。
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
    const cfg = loadSrConfig();
    try {
      const resp = await fetch(scenesListUrl(cfg, params()));
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const body = await resp.json() as {
        source: 'disk' | 'fake'; scanned: number; count: number; results: SceneRow[];
      };
      rows.value = body.results;
      source.value = body.source;
      scanned.value = body.scanned;
      count.value = body.count;
    } catch (e) {
      error.value = '场景检索失败：' + (e instanceof Error ? e.message : String(e));
      rows.value = [];
    } finally {
      loading.value = false;
    }
  }

  function resetFilters(): void {
    query.value = ''; satellite.value = ''; sensor.value = '';
    dateFrom.value = ''; dateTo.value = '';
    void list();
  }

  /** 打开场景：确保 JPG 已生成 → 静态 jpgUrl 读字节 → viewer.openSceneJpg。
      失败一律写本 store 的 error —— 本页（ScenesPage）只渲染 scenes.error，而
      viewer 的错误条挂在 /viewer、6 秒后自己消失；写错地方就等于按钮点了没反应。
      JPG 读不到 / 字节不是图（openSceneJpg 解码失败会抛，见 viewer.ts）都归这里。 */
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
      // 首次要服务端烘焙（读一遍大图，几十秒）。遮罩（viewer.showMask）只挂在
      // /viewer 上，本页调了也看不见，所以这里走自己的 phase 文案行。
      // 档位取 viewer store 里的那份（工具栏拖动条写的就是它）—— 这样用户拖完
      // 滑块不必刷新页面就生效；loadPreviewDiv() 只负责冷启动时的初值。
      const blob = await fetchSceneJpg(cfg, row, viewer.previewDiv,
                                       (text) => { phase.value = text; });
      await viewer.openSceneJpg({
        name: row.name, W: row.W, H: row.H, sceneId: row.id,
        // 裸 .tif 且父目录不是场景目录时后端把它置 null（= 不能提交 SR），
        // 这里不用再判 sr_capable —— 与 resolved.sr_capable 同源同真假。
        lqPath: row.lq_path,
        // 手工场景（resolvePath 进来的）：把后端推导的掩码路径一并带上，
        // 否则这条入口的 rec 少一个 serverMaskPath，「保存掩码到盘阵」前后
        // 显示的掩码路径与查看器那条入口不一致（两边最终都以后端回的为准）。
        serverMaskPath: resolved ? resolved.mask_path : null,
      }, blob);
    } catch (e) {
      error.value = '打开「' + row.name + '」失败：'
        + (e instanceof Error ? e.message : String(e));
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
   *  返回值是**错误串而不是布尔**：本函数的调用方在 /viewer，而 `open()` 按老规矩
   *  把失败写进 `scenes.error`，那个 ref 只在 ScenesPage 渲染 —— 在查看器上写它
   *  等于没写，用户只会看到「点了没反应」。所以由调用方拿去喂 viewer.showErr。
   *  返回 '' 表示成功。 */
  async function openByName(name: string): Promise<string> {
    const trimmed = String(name ?? '').trim();
    if (!trimmed) return '名字为空';
    error.value = '';
    try {
      const res = await apiResolveScene(loadSrConfig(), { name: trimmed });
      await open(res.row, res.resolved);
    } catch (e) {
      return '打开「' + trimmed + '」失败：' + (e instanceof Error ? e.message : String(e));
    }
    // open() 内部也会失败（fake 行 / 缺 W,H / 拉 JPG 出错），原因已经写在 error 里，
    // 原样带出去 —— 后端 resolve 的 detail 里有「试过哪些候选、各自为什么不行」。
    return error.value || ('打开「' + trimmed + '」失败');
  }

  return {
    rows, source, scanned, count, loading, openingId, phase, error,
    query, satellite, sensor, dateFrom, dateTo,
    satellites, sensors, dates,
    list, resetFilters, open, resolvePath, openByName,
  };
});
