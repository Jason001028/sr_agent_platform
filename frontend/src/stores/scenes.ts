/**
 * stores/scenes.ts — 盘阵场景检索/打开（阶段4）
 * ------------------------------------------------------------------
 * 显示 = 服务器预生成 JPG：列表由 GET /api/scenes 提供（后端补 W/H + jpgUrl），
 * 点击打开时若缓存 JPG 未生成先调 GET /api/scenes/{id}/preview 懒生成，再走静态
 * jpgUrl（nginx 直出）；字节交给 viewer store 构造 route='jpg' 的同构 rec。
 *
 * 运行期 base（apiBase/staticBase）每次从 loadSrConfig() 读取 —— 默认同源（nginx
 * 同时暴露 /api 与 /disk-array）；e2e/异源注入 window.__SR_CFG__。
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { useViewerStore } from './viewer.js';
import type { SceneRow, SceneQueryParams } from '../lib/scene.js';
import {
  loadSrConfig, scenesListUrl, scenePreviewUrl, sceneImageUrl,
} from '../lib/scene.js';

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

  /** 打开场景：确保 JPG 已生成 → 静态 jpgUrl 读字节 → viewer.openSceneJpg。 */
  async function open(row: SceneRow): Promise<void> {
    const viewer = useViewerStore();
    const cfg = loadSrConfig();
    if (row.fake) {
      viewer.showErr('fake 占位场景无真实文件，不可打开');
      return;
    }
    if (!row.W || !row.H) {
      viewer.showErr('「' + row.name + '」尺寸未知，无法换算掩码，拒绝打开');
      return;
    }
    openingId.value = row.id;
    error.value = '';
    try {
      // JPG 未生成 → 先懒生成（后端落盘），之后静态 jpgUrl 才可读
      if (!row.hasPreview || !row.jpgUrl) {
        const p = await fetch(scenePreviewUrl(cfg, row.id));
        if (!p.ok) {
          const detail = await p.text().catch(() => '');
          throw new Error('预览生成失败 HTTP ' + p.status + (detail ? ' · ' + detail : ''));
        }
        row.hasPreview = true;
      }
      const url = sceneImageUrl(cfg, row.jpgUrl);
      const img = await fetch(url);
      if (!img.ok) throw new Error('读 JPG 失败 HTTP ' + img.status);
      const blob = await img.blob();
      await viewer.openSceneJpg({
        name: row.name, W: row.W, H: row.H, sceneId: row.id, lqPath: row.lq_path,
      }, blob);
    } catch (e) {
      viewer.showErr('打开场景失败：' + (e instanceof Error ? e.message : String(e)));
    } finally {
      openingId.value = null;
    }
  }

  return {
    rows, source, scanned, count, loading, openingId, error,
    query, satellite, sensor, dateFrom, dateTo,
    satellites, sensors, dates,
    list, resetFilters, open,
  };
});
