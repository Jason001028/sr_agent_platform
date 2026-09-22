<script setup lang="ts">
/**
 * ScenesPage.vue — 场景库：盘阵场景检索 + 查看（阶段4）
 * 检索参数镜像后端 search_scenes（卫星/传感器/日期/关键词），行内 W/H 由后端
 * 补（.hdr 优先 / TIF 头探测 / JPG 走 Pillow 头）。点「打开」→ 未生成先懒生成预览
 * JPG → 静态 jpgUrl 读字节 → viewer route='jpg'（掩码按元数据 W/H 换算）。
 * 盘阵里的 .jpg/.jpeg 源（§4.7）行 jpgUrl 即源文件、跳过懒生成，打标签「JPG 源」。
 */
import { onMounted } from 'vue';
import { isImageSource, previewNeedsBake, presumedPurged } from '../lib/scene.js';
import { useScenesStore } from '../stores/scenes.js';
import { useViewerStore } from '../stores/viewer.js';
import ScenePathBar from '../components/ScenePathBar.vue';
import SceneCacheBar from '../components/SceneCacheBar.vue';

const scenes = useScenesStore();
// 只为了读当前预览档位（工具栏那条拖动条写的就是它）——「已生成 / 未生成」得认档位：
// 盘上有旧档位的图时 `hasPreview` 仍为真，只看它就会说「已生成」，点下去却要等一輪重烤。
// 注意「打开」那一格**不看档位**了：它只分「按已自动清除处理」（presumedPurged）与
// 「打开」两种，能点的行一律写「打开」—— 要烤的话按下去就烤。
const viewer = useViewerStore();

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

/** 「上次检索」读数的时刻：当天只给 HH:MM，跨天带上 MM-DD。
 *  列表不再每次进入都刷新（见 store.ensureSearched），所以得让用户看得出这份数据
 *  是什么时候的 —— 别把它当成刚扫过的盘阵。 */
function stampText(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  const hm = `${p(d.getHours())}:${p(d.getMinutes())}`;
  const now = new Date();
  const sameDay = d.getFullYear() === now.getFullYear()
    && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
  return sameDay ? hm : `${p(d.getMonth() + 1)}-${p(d.getDate())} ${hm}`;
}

// **只在本次会话第一次进本页时**检索：每次进入都重检索会把上一轮「清除缓存」的结果
// 当场抹掉（摘掉的行全回来、汇总行消失），用户看到的就是「清除没生效」（见 store 里
// ensureSearched 的注释）。要拿最新的盘阵数据按「检索」。
onMounted(() => { scenes.ensureSearched(); });
</script>

<template>
  <div class="scenes-page">
    <div class="sp-head">
      <h2>场景库</h2>
      <span class="sp-src" :class="scenes.source === 'fake' ? 'fake' : 'disk'">
        {{ scenes.source === 'fake' ? 'fake 回退（未配 SR_SCENES_ROOT）' : '盘阵' }}
        · 扫 {{ scenes.scanned }} · 命中 {{ scenes.count }}
      </span>
      <!-- 列表会留在页面上直到用户按「检索」（见 onMounted），所以带上这次检索的
           时刻：否则「新场景怎么不出现」没有答案。 -->
      <span v-if="scenes.searchedAt" class="sp-when"
            title="这份列表是上一次检索的结果；要拿盘阵上最新的数据请按「检索」">
        上次检索 {{ stampText(scenes.searchedAt) }}
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
      <SceneCacheBar />
      <table class="sp-tbl">
        <thead>
          <tr>
            <!-- 首列：勾选（清缓存的选中态）。表头留空 —— 全选复选框在 SceneCacheBar
                 上，「已选 N 项」也在那儿，表头再放一颗会变成两个「全选」。 -->
            <th class="c-pick"></th>
            <th class="c-sat">卫星</th><th class="c-sensor">传感器</th>
            <th class="c-date">日期</th>
            <th class="left c-name">场景（文件）</th><th class="right c-dims">尺寸</th>
            <th class="right c-size">大小</th><th class="c-tag">预览 JPG</th>
            <th class="c-act">打开</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in scenes.rows" :key="row.id">
            <td class="c-pick">
              <!-- 必须是 input[type=checkbox]：e2e 里 `tr.querySelector('button')`
                   取的是「打开」那颗按钮，放个 button 形状的勾选框会把它顶掉。 -->
              <input v-if="scenes.isSelectable(row)" type="checkbox"
                     class="sp-pick" :checked="scenes.selected.has(row.id)"
                     :disabled="scenes.clearing"
                     :title="'选中「' + row.name + '」（清除缓存用）'"
                     @change="scenes.toggleSelect(row.id)" />
              <span v-else class="sp-nopick" title="fake 占位行在盘上没有文件，不可清除">—</span>
            </td>
            <td class="c-sat">{{ row.satellite ?? '—' }}</td>
            <td class="c-sensor">{{ row.sensor ?? '—' }}</td>
            <td class="c-date">{{ row.date ?? '—' }}</td>
            <td class="left name c-name" :title="row.rel ?? ''">{{ row.name }}</td>
            <td class="right c-dims">{{ dimsText(row) }}</td>
            <td class="right c-size">{{ fmtBytes(row.size_bytes) }}</td>
            <td class="c-tag">
              <!-- 按「盘上数据已被自动清除」处理的行：标签栏也得跟着改口径 ——
                   旁边那颗「已自动清除」与「已生成 / 未生成」摆在一起是自相矛盾的。 -->
              <span v-if="presumedPurged(row)" class="tag gone"
                    title="按「盘阵上的数据已被自动清除」处理：盘上没有这一景的预览（多半产出后几天就被清掉了），或者打开时真的撞上了 404">已清除</span>
              <span v-else-if="row.fake" class="tag fake">fake</span>
              <span v-else-if="isImageSource(row)" class="tag ok">JPG 源</span>
              <span v-else-if="!previewNeedsBake(row, viewer.previewDiv)"
                    class="tag ok">已生成</span>
              <span v-else class="tag">未生成</span>
            </td>
            <td class="c-act">
              <!-- 数据多半已经被自动清除的行（presumedPurged 那一格）：这一格换成不可点的
                   灰块，别再让人对着同一堵墙点第二遍。仍然是 `<button disabled>` 而不是
                   div —— e2e 的 rows() 按 `td.c-act button` 读这一格（也会按 disabled
                   判断它不可点）。 -->
              <button v-if="presumedPurged(row)" type="button" class="btn mini gone" disabled
                      title="按「盘阵上的数据已被自动清除」处理：盘上没有这一景的预览（多半产出后几天就被清掉了），或者打开时真的撞过 404。列表是上次检索的快照，按「检索」刷新即知它还在不在；若确认数据仍在，把场景路径粘到上方路径栏仍可打开">
                已自动清除
              </button>
              <button v-else type="button" class="btn mini"
                      :disabled="scenes.openingId === row.id" @click="scenes.open(row)">
                {{ scenes.openingId === row.id ? '打开中…' : '打开' }}
              </button>
            </td>
          </tr>
          <tr v-if="!scenes.loading && !scenes.rows.length">
            <!-- 空表有两个来源，不能都写成「没有匹配场景」：清除缓存把行摘光时，
                 盘阵上这些场景明明还在，说「没有」是假话（重新检索就回来）。 -->
            <td colspan="9" class="empty">
              {{ scenes.clearRemoved
                 ? '这批已从列表移除（场景与源文件都还在盘阵上），重新检索即回来'
                 : '没有匹配场景（或未连接盘阵）' }}
            </td>
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
/* 「上次检索 HH:MM」：中性小字，与来源 chip 同排 —— 它不是状态，只是读数 */
.sp-when { font-size: 12px; color: var(--ink-sub); }

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
/* 「已自动清除」：盘阵上文件已经没了（或按年龄推定已没了）的行。不是「禁用中的按钮」（那颗看着像还能救），
   是一块**已经把话说完的灰块** —— 所以显式压掉 .btn:disabled 的半透明，让字读得清；
   cursor 保持 not-allowed，鼠标移上去不给人「或许能点」的错觉。 */
.btn.mini.gone {
  background: var(--surface-2);
  color: var(--ink-sub);
  border-color: var(--line);
  box-shadow: none;
  opacity: 1;
  cursor: not-allowed;
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
/* 勾选列压到最窄：本表已有 8 列长内容，第 9 列多占一像素都可能把
   .sp-tbl-wrap（overflow:hidden）里的表尾挤没。 */
.sp-tbl th.c-pick, .sp-tbl td.c-pick { width: 34px; padding: 9px 6px; }
.sp-pick { width: 14px; height: 14px; margin: 0; cursor: pointer; vertical-align: middle; }
.sp-nopick { color: var(--ink-faint); }
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
/* 打开时撞过 404 的行：盘阵上已经没有这一景的文件（与旁边那块「已自动清除」同义） */
.tag.gone { background: var(--surface-2); color: var(--ink-sub); border-color: var(--line); }
.sp-loading { color: var(--ink-sub); font-size: 12px; }
/* 打开阶段文案：与 sp-err 同一个位置，但用中性色 —— 它不是错误 */
.sp-phase { margin: 0 0 10px; line-height: 1.6; }
</style>
