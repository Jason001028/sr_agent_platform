<script setup lang="ts">
/**
 * ScenesPage.vue — 场景库：盘阵场景检索 + 查看（阶段4）
 * 检索参数镜像后端 search_scenes（卫星/传感器/日期/关键词），行内 W/H 由后端
 * 补（.hdr 优先 / TIF 头探测 / JPG 走 Pillow 头）。点「打开」→ 未生成先懒生成预览
 * JPG → 静态 jpgUrl 读字节 → viewer route='jpg'（掩码按元数据 W/H 换算）。
 * 盘阵里的 .jpg/.jpeg 源（§4.7）行 jpgUrl 即源文件、跳过懒生成，打标签「JPG 源」。
 */
import { onMounted } from 'vue';
import { isImageSource } from '../lib/scene.js';
import { useScenesStore } from '../stores/scenes.js';
import ScenePathBar from '../components/ScenePathBar.vue';

const scenes = useScenesStore();

/** 手工路径：只把用户填的这一个目录交给后端 stat（不扫盘）。错误由本页
 *  统一渲染（scenes.error，就在检索条下方），所以这里不用再弹提示。 */
function openPastedPath(path: string): void {
  void scenes.resolvePath(path);
}

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
      <h2>场景库</h2>
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

    <ScenePathBar class="sp-path" :busy="!!scenes.openingId" @open="openPastedPath" />

    <p v-if="scenes.error" class="sp-err">{{ scenes.error }}</p>
    <!-- 首次打开要在服务端烘焙（读一遍大图，几十秒）。遮罩只挂在 /viewer 上，
         本页没有遮罩，就用这一行说明在忙什么，别让按钮一直停在「打开中…」。 -->
    <p v-if="scenes.phase" class="sp-loading sp-phase">{{ scenes.phase }}</p>

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
              <span v-else-if="isImageSource(row)" class="tag ok">JPG 源</span>
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

  </div>
</template>

<style scoped>
/* 盘阵场景检索：莫兰迪绿打底，检索条与结果表为白色浮层卡 */
.scenes-page { max-width: var(--page-w); margin: 0 auto; padding: 10px 20px 44px; }

/* 页头：大标题（藏青）直接落在莫兰迪底上，来源 chip 用白/浅胶囊 */
.sp-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin: 0 0 16px; }
.sp-head h2 { margin: 0; font-size: 24px; font-weight: 700; color: var(--ink); }
.sp-src {
  font-size: 12px;
  color: var(--ok);
  background: var(--ok-bg);
  border: 1px solid var(--ok-line);
  padding: 4px 12px;
  border-radius: var(--r-pill);
  white-space: nowrap;
}
.sp-src.fake { color: var(--warn); background: var(--warn-bg); border-color: var(--warn-line); }

/* 检索条：白色卡片 */
.sp-filters {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
  margin-bottom: 14px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-panel);
  box-shadow: var(--shadow-card);
  padding: 12px 14px;
}
.sp-in {
  height: 34px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-ctrl);
  font-size: 13px;
  color: var(--ink);
  background: var(--surface-2);
  min-width: 130px;
  outline: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease, background 0.15s ease;
  font-family: inherit;
}
.sp-in::placeholder { color: var(--ink-faint); }
.sp-in:hover { border-color: #d8dfdc; }
.sp-in:focus { border-color: var(--accent-3); background: var(--surface); box-shadow: 0 0 0 3px rgba(45, 164, 162, 0.14); }
.sp-in.sp-date { min-width: 0; width: 150px; }
.sp-sep { color: var(--ink-faint); font-weight: 600; }

/* 按钮：检索为主 CTA（渐变）；其余次级（白底细边框） */
.btn {
  height: 34px;
  padding: 0 16px;
  border: 1px solid transparent;
  border-radius: var(--r-ctrl);
  background: var(--accent-grad);
  color: #fff;
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  font-family: inherit;
  transition: filter 0.15s ease, box-shadow 0.15s ease;
  box-shadow: 0 2px 6px rgba(45, 164, 162, 0.22);
}
.btn:hover { filter: brightness(1.05); }
.btn:disabled { opacity: .5; cursor: not-allowed; box-shadow: none; }
.btn.ghost {
  background: var(--surface);
  color: var(--ink-body);
  border-color: var(--line);
  box-shadow: none;
}
.btn.ghost:hover { color: var(--accent-deep); border-color: var(--accent-2); }
.btn.mini {
  height: 26px;
  padding: 0 12px;
  font-size: 12px;
  font-weight: 500;
}

.sp-path { margin-bottom: 10px; }
.sp-err { color: var(--err); font-size: 13px; margin: 0 0 10px; white-space: pre-wrap; }

/* 结果表：白色卡片，圆角裁掉表头直角 */
.sp-tbl-wrap {
  position: relative;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-panel);
  box-shadow: var(--shadow-card);
  overflow: hidden;
}
.sp-tbl {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  font-size: 13px;
  background: var(--surface);
  color: var(--ink-body);
}
.sp-tbl th, .sp-tbl td {
  padding: 9px 12px;
  text-align: center;
  border-bottom: 1px solid var(--line);
}
.sp-tbl th {
  background: var(--surface-2);
  color: var(--ink);
  font-weight: 600;
  white-space: nowrap;
  letter-spacing: 0.2px;
}
.sp-tbl td.left { text-align: left; }
.sp-tbl td.right { text-align: right; font-variant-numeric: tabular-nums; }
/* 场景名完整显示：不截断、不省略号。生产场景名是「卫星_传感器_时间戳_…_L1_PAN」
   这种 50+ 字符的长串，中间没有空格，靠 table 布局自己挤不出空间，必须允许换行
   并按字符断行（word-break），否则又被 ellipsis 截掉。 */
.sp-tbl td.name { min-width: 300px; word-break: break-all; line-height: 1.45; }
.sp-tbl td.empty { color: var(--ink-sub); text-align: center; padding: 22px; }
.sp-tbl tbody tr { transition: background 0.12s ease; }
.sp-tbl tbody tr:hover td { background: #fafcfa; }
.sp-tbl tbody tr:last-child td { border-bottom: none; }

.tag {
  font-size: 11px;
  padding: 2px 9px;
  border-radius: var(--r-pill);
  background: var(--surface-2);
  color: var(--ink-sub);
  border: 1px solid transparent;
  white-space: nowrap;
}
.tag.ok { background: var(--ok-bg); color: var(--ok); border-color: var(--ok-line); }
.tag.fake { background: var(--warn-bg); color: var(--warn); border-color: var(--warn-line); }
.sp-loading { color: var(--ink-sub); font-size: 12px; }
/* 打开阶段文案：与 sp-err 同一个位置，但用中性色 —— 它不是错误 */
.sp-phase { margin: 0 0 10px; line-height: 1.6; }
</style>
