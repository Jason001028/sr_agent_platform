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
      // 库行走静态 jpgUrl、库外场景走 /preview 响应体，两条来源都在这里收口
      const blob = await fetchSceneJpg(cfg, row);
      await viewer.openSceneJpg({
        name: row.name, W: row.W, H: row.H, sceneId: row.id, lqPath: row.lq_path,
        // 手工场景（resolvePath 进来的）：把后端推导的掩码路径一并带上，
        // 否则这条入口的 rec 少一个 serverMaskPath，「保存掩码到盘阵」前后
        // 显示的掩码路径与查看器那条入口不一致（两边最终都以后端回的为准）。
        serverMaskPath: resolved ? resolved.mask_path : null,
      }, blob);
    } catch (e) {
      error.value = '打开「' + row.name + '」失败：'
        + (e instanceof Error ? e.message : String(e));
    } finally {
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

  return {
    rows, source, scanned, count, loading, openingId, error,
    query, satellite, sensor, dateFrom, dateTo,
    satellites, sensors, dates,
    list, resetFilters, open, resolvePath,
  };
});
