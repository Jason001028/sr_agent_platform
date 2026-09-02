<script setup lang="ts">
/**
 * ScenesPage.vue — 盘阵场景检索 + 查看（阶段4）
 * 检索参数镜像后端 search_scenes（卫星/传感器/日期/关键词），行内 W/H 由后端
 * 补（.hdr 优先 / TIF 头探测）。点「打开」→ 未生成先懒生成预览 JPG → 静态 jpgUrl
 * 读字节 → viewer route='jpg'（掩码按元数据 W/H 换算）。
 */
import { onMounted } from 'vue';
import { useScenesStore } from '../stores/scenes.js';
import { useViewerStore } from '../stores/viewer.js';

const scenes = useScenesStore();
const viewer = useViewerStore();

function fmtBytes(n: number): string {
  if (n >= 1073741824) return (n / 1073741824).toFixed(2) + ' GB';
  if (n >= 1048576) return (n / 1048576).toFixed(1) + ' MB';
  if (n >= 1024) return (n / 1024).toFixed(1) + ' KB';
  return n + ' B';
}

function dimsText(row: { W: number | null; H: number | null }): string {
  return row.W && row.H ? `${row.W}×${row.H}` : '—';
}

onMounted(() => { void scenes.list(); });
</script>

<template>
  <div class="scenes-page">
    <div class="sp-head">
      <h2>盘阵场景</h2>
      <span class="sp-src" :class="scenes.source === 'fake' ? 'fake' : 'disk'">
        {{ scenes.source === 'fake' ? 'fake 回退（未配 SR_SCENES_ROOT）' : '盘阵' }}
        · 扫 {{ scenes.scanned }} · 命中 {{ scenes.count }}
      </span>
    </div>

    <div class="sp-filters">
      <input v-model="scenes.query" class="sp-in" type="text"
             placeholder="关键词（文件名片段）" @keyup.enter="scenes.list()" />
      <input v-model="scenes.satellite" class="sp-in" type="text" list="sp-sats"
             placeholder="卫星（如 GF07A03）" @keyup.enter="scenes.list()" />
      <datalist id="sp-sats">
        <option v-for="s in scenes.satellites" :key="s" :value="s" />
      </datalist>
      <input v-model="scenes.sensor" class="sp-in" type="text" list="sp-sensors"
             placeholder="传感器（如 PMS01）" @keyup.enter="scenes.list()" />
      <datalist id="sp-sensors">
        <option v-for="s in scenes.sensors" :key="s" :value="s" />
      </datalist>
      <input v-model="scenes.dateFrom" class="sp-in sp-date" type="date" title="起始日期" />
      <span class="sp-sep">→</span>
      <input v-model="scenes.dateTo" class="sp-in sp-date" type="date" title="截止日期" />
      <button type="button" class="btn" :disabled="scenes.loading" @click="scenes.list()">检索</button>
      <button type="button" class="btn ghost" :disabled="scenes.loading" @click="scenes.resetFilters()">重置</button>
      <button type="button" class="btn ghost" :disabled="scenes.loading"
              title="打开 /viewer 查看当前激活场景" @click="$router.push('/viewer')">去查看器</button>
    </div>

    <p v-if="scenes.error" class="sp-err">{{ scenes.error }}</p>

    <div class="sp-tbl-wrap">
      <table class="sp-tbl">
        <thead>
          <tr>
            <th>卫星</th><th>传感器</th><th>日期</th>
            <th class="left">场景（文件）</th><th class="right">尺寸</th>
            <th class="right">大小</th><th>预览 JPG</th><th>打开</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in scenes.rows" :key="row.id">
            <td>{{ row.satellite ?? '—' }}</td>
            <td>{{ row.sensor ?? '—' }}</td>
            <td>{{ row.date ?? '—' }}</td>
            <td class="left name" :title="row.rel ?? ''">{{ row.name }}</td>
            <td class="right">{{ dimsText(row) }}</td>
            <td class="right">{{ fmtBytes(row.size_bytes) }}</td>
            <td>
              <span v-if="row.fake" class="tag fake">fake</span>
              <span v-else-if="row.hasPreview" class="tag ok">已生成</span>
              <span v-else class="tag">未生成</span>
            </td>
            <td>
              <button type="button" class="btn mini" :disabled="scenes.openingId === row.id"
                      @click="scenes.open(row)">
                {{ scenes.openingId === row.id ? '打开中…' : row.hasPreview ? '打开' : '生成并打开' }}
              </button>
            </td>
          </tr>
          <tr v-if="!scenes.loading && !scenes.rows.length">
            <td colspan="8" class="empty">没有匹配场景（或未连接盘阵）</td>
          </tr>
        </tbody>
      </table>
      <p v-if="scenes.loading" class="sp-loading">检索中…</p>
    </div>

    <p class="sp-hint">
      场景 JPG 为服务器烘焙（稀疏采样 + 2% 线性拉伸），打开后掩码按元数据
      {{ viewer.activeRec?.route === 'jpg' ? dimsText(viewer.activeRec) : 'W/H' }} 换算回全分辨率；
      交互式拉伸/导出 JPG 在场景路径不可用（本地 TIF 路径照旧）。
    </p>
  </div>
</template>

<style scoped>
.scenes-page { max-width: 1100px; margin: 0 auto; padding: 18px 16px 40px; }
.sp-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.sp-head h2 { margin: 0 0 10px; font-size: 18px; }
.sp-src { font-size: 12px; color: #67c23a; }
.sp-src.fake { color: #e6a23c; }
.sp-filters { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }
.sp-in { padding: 5px 8px; border: 1px solid #c0c4cc; border-radius: 4px; font-size: 13px; min-width: 120px; }
.sp-in.sp-date { min-width: 0; width: 150px; }
.sp-sep { color: #909399; }
.btn { padding: 5px 12px; border: 1px solid #409eff; border-radius: 4px; background: #409eff; color: #fff; cursor: pointer; font-size: 13px; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.btn.ghost { background: transparent; color: #409eff; }
.btn.mini { padding: 2px 8px; font-size: 12px; }
.sp-err { color: #f56c6c; font-size: 13px; }
.sp-tbl-wrap { position: relative; }
.sp-tbl { width: 100%; border-collapse: collapse; font-size: 13px; background: #fff; }
.sp-tbl th, .sp-tbl td { border: 1px solid #ebeef5; padding: 6px 8px; text-align: center; }
.sp-tbl th { background: #f5f7fa; font-weight: 600; white-space: nowrap; }
.sp-tbl td.left { text-align: left; }
.sp-tbl td.right { text-align: right; font-variant-numeric: tabular-nums; }
.sp-tbl td.name { max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sp-tbl td.empty { color: #909399; text-align: center; padding: 18px; }
.tag { font-size: 11px; padding: 1px 6px; border-radius: 3px; background: #f4f4f5; color: #909399; white-space: nowrap; }
.tag.ok { background: #f0f9eb; color: #67c23a; }
.tag.fake { background: #fdf6ec; color: #e6a23c; }
.sp-loading { color: #909399; font-size: 12px; }
.sp-hint { color: #909399; font-size: 12px; margin-top: 10px; }
</style>
