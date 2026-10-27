<script setup lang="ts">
/**
 * AgentChatTab.vue — 上下文侧舱 [Agent] tab（阶段6 L2）
 * ------------------------------------------------------------------
 * - 仅盘阵场景可用（activeRec.sceneId 非空）；本地/离线 TIF 或未开图 → 禁用并给出原因。
 * - 与 /chat **同一** chat store / 会话 / 后端。这里**不 init**（进查看器不自动挑会话，
 *   首问才 apiCreateSession），但头部给了会话三件事（三个 16px 图标键，2026-10-23）：
 *   新建（circle-plus）/ 历史（clock，点开切换）/ 删除当前（circle-x）。历史列表
 *   **打开菜单时才拉**（`refreshSessions`），不用会话管理就一个请求都不多发。
 *   原先头部左侧那串「会话 xxxxxxxx」已去掉 —— 它对操作没有帮助，当前会话在历史列表里
 *   高亮即已说明。角落保留「去 Chat 页继续 ↗」。
 * - 每个发送 = agentPayload(question, buildContextNote(snap)) —— question 原样 + CTX_DIVIDER
 *   + 自动上下文快照（图像名 / 原图 W×H / 显示尺寸 / 拉伸 + 选中 ROI 的确定性统计）。
 *   Agent 只解读/建议，数字一律来自 buildStats，不得编造（见 agentContext.ts 纪律）。
 * - 渲染形状与 /chat 一致（SSE 归并出的 ChatMsg）；工具回合折成单行摘要。用户气泡只显示
 *   原话：CTX_DIVIDER 之后的自动附文默认不进渲染，只有开发者模式在气泡下给一块浅米色
 *   折叠面板（`<details>`，默认收起）—— 排查「Agent 到底看到了什么」时才展开。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useViewerStore } from '../stores/viewer';
import { useChatStore } from '../stores/chat.js';
import type { ChatMsg } from '../stores/chat.js';
import MarkdownBody from './MarkdownBody.vue';
import ReasoningBlock from './ReasoningBlock.vue';
import { phaseLabel } from '../lib/executionOverview.js';
import ThinkingIndicator from './ThinkingIndicator.vue';
import ChatAttachTray from './ChatAttachTray.vue';
import ChatImagePicker from './ChatImagePicker.vue';
import ChatBubbleImages from './ChatBubbleImages.vue';
import AppIcon from './AppIcon.vue';
import SceneSearchResultCard from './SceneSearchResultCard.vue';
import { useScenesStore } from '../stores/scenes.js';
import {
  agentPayload, buildContextNote, splitAgentPayload,
} from '../lib/agentContext.js';
import type { ViewerContextSnap } from '../lib/agentContext.js';
import { isDeveloperMode } from '../lib/developerMode.js';
import { roiOrigGeom } from '../lib/roiStats.js';

const viewer = useViewerStore();
const chat = useChatStore();
const scenes = useScenesStore();
const draft = ref('');
const scrollEl = ref<HTMLElement | null>(null);
/** 截图选择器（截查看器当前画面，来源是左侧影像列表）。 */
const pickerOpen = ref(false);
async function openSearchScene(row: Record<string, unknown>): Promise<void> {
  const name = String(row.name ?? row.sceneId ?? row.id ?? '').trim();
  if (name) await scenes.openByName(name);
}

const rec = computed(() => viewer.activeRec);
/** 仅盘阵场景可用；无图 / 本地(无 sceneId) → false 并给 reason。 */
const enabled = computed(() => Boolean(rec.value));
/** 无会话时首问会自动建会话 —— 输入框始终可输入；placeholder 提示差异。 */
const placeholder = computed(() =>
  chat.sessionId
    ? '问 Agent（自动附当前 ROI 统计）…'
    : '输入问题即自动新建会话并附上下文…');
const reason = computed(() => {
  const r = rec.value;
  if (!r) return '当前未打开图像。先从左侧打开一个文件，或进「打开场景」选盘阵场景。';
  return '';
});

/** 当前查看器确定性上下文快照（禁用时 null；每次发送重新取，保证数字最新）。 */
const snap = computed<ViewerContextSnap | null>(() => {
  const r = rec.value;
  if (!r) return null;
  const dispW = r.thumb ? r.thumb.width : 0;
  const dispH = r.thumb ? r.thumb.height : 0;
  // 报**这张图当前显示出来**的那个模式（viewer.activeStretch）。盘阵场景的底图
  // 是服务端生成的直方图均衡，但显示层可能已经二次拉伸过，Agent 看到的是后者。
  const mode = viewer.activeStretch;
  const stretch = STRETCH_LABEL[mode] ?? mode;
  let roi: ViewerContextSnap['roi'] = null;
  const si = viewer.roiSelIndex();
  const stats = viewer.roiStats;
  if (si >= 0 && stats && r.maskRois && si < r.maskRois.length && dispW && dispH) {
    roi = {
      index: si + 1,
      geom: roiOrigGeom(r.maskRois[si], r.W, r.H, dispW, dispH),
      stats,
    };
  }
  return { name: r.name, sceneId: r.sceneId, W: r.W, H: r.H, dispW, dispH, stretch, roi };
});

const STRETCH_LABEL: Record<string, string> = {
  linear: '线性', linear2: '2% 线性', sqrt: '平方根', log: '对数', equal: '直方图均衡',
};

/* ---------------- 消息渲染 ---------------- */
function shortId(sid: string): string {
  return sid.length > 10 ? sid.slice(0, 8) + '…' : sid;
}
function fmtTime(ts: number): string {
  const d = new Date(ts * 1000);
  const pad = (n: number): string => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/* ---------------- 会话：新建 / 切换历史 / 删除当前 ----------------
 * 与 /chat 是同一个 store，两边看到同一份会话与同一条当前会话。
 * 历史列表**按需拉**（打开菜单那一刻 refreshSessions）：查看器不该因为右侧挂着一块
 * Agent 面板就每次进页多打一趟 /api/chat/sessions；不用会话管理就一个请求都不发。 */
const menuOpen = ref(false);
const sessLoading = ref(false);
// 菜单开着时换成了一张本地图（Agent 不可用）→ 收掉，别留一个悬空的下拉
watch(enabled, (ok) => { if (!ok) menuOpen.value = false; });

async function openMenu(): Promise<void> {
  menuOpen.value = true;
  sessLoading.value = true;
  try {
    await chat.refreshSessions();
  } catch {
    // 列表取不到不影响当前会话；发送/加载链路的错误照常显示在顶部
  } finally {
    sessLoading.value = false;
  }
}

function toggleMenu(): void {
  if (menuOpen.value) menuOpen.value = false;
  else void openMenu();
}

async function startNew(): Promise<void> {
  if (chat.sending || creating.value) return;
  menuOpen.value = false;
  await chat.newSession();
}

async function switchTo(sid: string): Promise<void> {
  menuOpen.value = false;
  if (chat.sending || sid === chat.sessionId) return;
  await chat.selectSession(sid);
}

/** 删除**当前**会话；确认文案与 /chat 行内删除同一口径（消息/用量/轨迹一并删）。 */
async function removeCurrent(): Promise<void> {
  const sid = chat.sessionId;
  if (!sid || chat.sending) return;
  const ok = window.confirm(
    `删除会话 ${shortId(sid)}？\n消息、用量和轨迹记录会一并删除，且不可恢复。`);
  if (!ok) return;
  menuOpen.value = false;
  await chat.deleteSession(sid);
}

function onMenuKey(e: KeyboardEvent): void {
  if (e.key === 'Escape' && menuOpen.value) menuOpen.value = false;
}
onMounted(() => window.addEventListener('keydown', onMenuKey));
onBeforeUnmount(() => window.removeEventListener('keydown', onMenuKey));
function msgKey(m: ChatMsg, i: number): string {
  return m.seq !== null ? 's' + m.seq : 'x' + i;
}
/** 用户气泡只显示原话：自动附的查看器上下文只发给 Agent，不上屏（与 ChatPage 同口径）。 */
function userText(content: string): string {
  return splitAgentPayload(content).question;
}
/** 自动附文（CTX_DIVIDER 之后）；历史 /chat 消息没有分隔线 → 空串，不给开发者面板。 */
function ctxNote(content: string): string {
  return splitAgentPayload(content).note;
}
/** 开发者模式（本机 localStorage，与设置浮层同源）。设置浮层关掉时重读一次：
 *  在浮层里刚开/关完，回来这块面板要跟着变。 */
const developerMode = ref(false);
function readDeveloperMode(): void {
  developerMode.value = isDeveloperMode();
}
onMounted(readDeveloperMode);
watch(() => viewer.settingsOpen, (open) => { if (!open) readDeveloperMode(); });
function toolIconName(name: string): string {
  const icons: Record<string, string> = {
    search_scenes: 'search',
    run_sr: 'file-code',
  };
  return icons[name] ?? 'hammer';
}
function toolResultAt(index: number, name: string): ChatMsg | null {
  const next = chat.messages[index + 1];
  return next?.role === 'tool' && next.toolName === name ? next : null;
}
function toolResultsAt(index: number, name: string): ChatMsg[] {
  const out: ChatMsg[] = [];
  for (let j = index + 1; j < chat.messages.length && chat.messages[j].role === 'tool'; j++) if (chat.messages[j].toolName === name) out.push(chat.messages[j]);
  return out;
}

/** 队尾仍在流式接收的 assistant 消息（思考/回答/工具声明阶段指示用）。 */
const liveMsg = computed<ChatMsg | null>(() => {
  const last = chat.messages[chat.messages.length - 1];
  return last && last.role === 'assistant' && last.streaming ? last : null;
});

function waitLabel(m: ChatMsg): string {
  return phaseLabel(m.phase);
}

const creating = ref(false);   // 防止双击并发 newSession 建出两个会话

/** 有截图时可以不写字：上下文快照照样附上（Agent 仍能引用 W×H / 拉伸）。 */
function canSend(): boolean {
  return enabled.value && snap.value !== null && !chat.sending && !creating.value
    && (Boolean(draft.value.trim()) || chat.pendingImages.length > 0);
}

async function onSend(): Promise<void> {
  const text = draft.value.trim();
  const s = snap.value;
  if (!canSend() || !s) return;
  if (!chat.sessionId) {
    // 首问自动建会话（与 /chat 同一 store；newSession 失败会在 error 区显示）
    creating.value = true;
    try {
      await chat.newSession();
    } finally {
      creating.value = false;
    }
    if (!chat.sessionId) return;
  }
  draft.value = '';
  const latest = snap.value;
  if (latest) void chat.send(agentPayload(text, buildContextNote(latest)));
}

function nearBottom(): boolean {
  const el = scrollEl.value;
  return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 60;
}
async function scrollBottom(): Promise<void> {
  await nextTick();
  if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight;
}
watch(() => chat.messages.length, () => void scrollBottom());
// 流式增量不新增消息，只改长度：已经贴着底部才跟随，用户翻上去就不拽回来。
watch(() => chat.streamTick, () => { if (nearBottom()) void scrollBottom(); });
</script>

<template>
  <div class="at">
    <!-- 头部：会话操作 + 去 Chat 页。不再显示「会话 xxxxxxxx」：当前会话在
         历史菜单里高亮，那串 id 对操作没有帮助。 -->
    <div class="at-head">
      <!-- Agent 不可用时（本地/离线图、未开图）不摆会话按钮：新会话、切/删会话在这里
           都没有落点，留着只会点出一个空会话。历史管理回 /chat 做。 -->
      <div v-if="enabled" class="at-sess-tools">
        <button type="button" class="at-hbtn" data-e2e="agent-new"
                :disabled="chat.sending || creating"
                title="新建会话（当前会话保留在历史里）" aria-label="新建会话"
                @click="startNew">
          <AppIcon name="circle-plus" :size="16" />
        </button>
        <button type="button" class="at-hbtn" :class="{ on: menuOpen }"
                data-e2e="agent-history" :disabled="chat.sending"
                title="历史会话：点开切换" aria-label="历史会话"
                @click="toggleMenu">
          <AppIcon name="clock" :size="16" />
        </button>
        <button type="button" class="at-hbtn danger" data-e2e="agent-del"
                :disabled="!chat.sessionId || chat.sending"
                :title="chat.sessionId
                  ? '删除当前会话（消息、用量和轨迹一并删除）'
                  : '当前没有会话'"
                aria-label="删除当前会话"
                @click="removeCurrent">
          <AppIcon name="circle-x" :size="16" />
        </button>
      </div>
      <router-link class="at-go" to="/chat">去 Chat 页继续 ↗</router-link>
    </div>

    <!-- 会话菜单（下拉）+ 点外部关闭的透明捕手 -->
    <template v-if="menuOpen && enabled">
      <div class="at-menu-catch" @click="menuOpen = false"></div>
      <div class="at-menu" role="menu" aria-label="历史会话">
        <div class="at-menu-cap">历史会话</div>
        <p v-if="sessLoading && !chat.sessions.length" class="at-menu-empty">载入中…</p>
        <p v-else-if="!chat.sessions.length" class="at-menu-empty">暂无历史会话。</p>
        <ul v-else class="at-sess-list">
          <li v-for="s in chat.sessions" :key="s.session_id">
            <button type="button" class="at-sess-item"
                    :class="{ cur: s.session_id === chat.sessionId }"
                    :title="'更新时间 ' + new Date(s.updated_at * 1000).toLocaleString()"
                    @click="switchTo(s.session_id)">
              <span class="at-sess-id">
                <span v-if="s.pinned" class="at-pin">置顶</span>{{ s.metadata?.title || shortId(s.session_id) }}
              </span>
              <span class="at-sess-time">{{ fmtTime(s.updated_at) }}</span>
            </button>
          </li>
        </ul>
      </div>
    </template>

    <p v-if="chat.error" class="at-err">{{ chat.error }}</p>

    <!-- 禁用原因（本地/离线/未开图） -->
    <div v-if="!enabled" class="at-disabled">
      <p class="at-desc">Agent服务仅对盘阵生产目录开放。</p>
      <p class="at-reason">{{ reason }}</p>
    </div>

    <!-- 会话区 -->
    <template v-else>
      <div ref="scrollEl" class="at-scroll">
        <div v-if="!chat.messages.length" class="at-empty">
          <p>在下方提问。首问会自动新建会话，并附当前查看器上下文（图像 + 选中 ROI 统计）。</p>
          <p class="at-hint">Agent 只解读画面上的确定性数字；涉及具体区域建议先在工具栏中框选。</p>
        </div>

        <div v-for="(m, i) in chat.messages" :key="msgKey(m, i)" class="at-row" :class="m.role">
          <!-- user：截图在上；原话为空（纯图消息）时不渲染空段 -->
          <template v-if="m.role === 'user'">
            <div class="bubble user" :class="{ 'has-shots': Boolean(m.images?.length) }">
              <ChatBubbleImages v-if="m.images?.length" :images="m.images" />
              <!-- 原话。自动附的查看器上下文（CTX_DIVIDER 之后）默认不上屏，只在开发者
                   模式下方折叠块里给；历史 /chat 消息没有分隔线，整条都是原话。 -->
              <p v-if="userText(m.content)" class="uq">{{ userText(m.content) }}</p>
            </div>
            <!-- 开发者模式才渲染自动附文：浅米色块，默认收起；普通模式完全不上屏。 -->
            <details v-if="developerMode && ctxNote(m.content)" class="ctx-dev">
              <summary>开发者模式 · 查看器附文</summary>
              <pre class="ctx-body">{{ ctxNote(m.content) }}</pre>
            </details>
          </template>

          <!-- assistant：推理折叠块 + 正文；工具声明并入同一条消息（与历史投影一致） -->
          <template v-else-if="m.role === 'assistant'">
            <div v-if="m.isError" class="bubble assistant err">{{ m.content }}</div>
            <template v-else>
              <ReasoningBlock v-if="m.reasoning" :text="m.reasoning"
                              :streaming="m.streaming" :phase="m.phase ?? null" :developer-mode="developerMode" />
              <div v-if="m.content" class="bubble assistant">
                <MarkdownBody :source="m.content" />
                <div v-if="m.toolCalls && m.toolCalls.length" class="tc-inline">
                  <span class="tc-label">Agent 调用：</span>
                  <span v-for="tool in (m.toolCalls ?? [])" :key="tool.id || tool.name" class="tc-tool">
                    <AppIcon :name="toolIconName(tool.name)" :size="15" :label="tool.name" />{{ tool.name }}
                  </span>
                </div>
              </div>
              <div v-else-if="m.toolCalls && m.toolCalls.length" class="agent-tool-list">
                <template v-for="tool in m.toolCalls" :key="tool.id || tool.name">
                  <SceneSearchResultCard v-if="tool.name === 'search_scenes'"
                    :args="tool.args" :data="toolResultAt(i, tool.name)?.data" :detail="toolResultAt(i, tool.name)?.detail"
                    :status="toolResultAt(i, tool.name)?.ok === false ? 'failed' : toolResultAt(i, tool.name)?.ok === true ? 'completed' : chat.sending ? 'running' : 'unconfirmed'"
                    @open="openSearchScene">
                    <details v-if="toolResultsAt(i, tool.name).length > 1" class="tool-history"><summary>查看调用记录</summary><div v-for="(result, n) in toolResultsAt(i, tool.name)" :key="n">{{ n + 1 }}. {{ result.detail }}</div></details>
                  </SceneSearchResultCard>
                  <div v-else class="bubble assistant tools">
                    <div class="tool-card"><div class="tool-card-head"><AppIcon :name="toolIconName(tool.name)" :size="17" /><strong>{{ tool.name === 'search_scenes' ? '检索目标场景' : tool.name }}</strong><code>{{ tool.name }}</code><span class="tool-state">{{ toolResultsAt(i, tool.name).length > 1 ? `共尝试 ${toolResultsAt(i, tool.name).length} 次` : (toolResultAt(i, tool.name)?.ok === true ? '✓ 完成' : toolResultAt(i, tool.name)?.ok === false ? '× 失败' : chat.sending ? '⏳ 调用中' : '结果未确认') }}</span></div><details class="tool-query"><summary>查询条件</summary><pre>{{ JSON.stringify(tool.args) }}</pre></details><div v-if="toolResultsAt(i, tool.name).length" class="tool-summary">最终结果：{{ toolResultsAt(i, tool.name).at(-1)?.detail }}</div><details v-if="toolResultsAt(i, tool.name).length > 1" class="tool-history"><summary>查看调用记录</summary><div v-for="(result, n) in toolResultsAt(i, tool.name)" :key="n">第 {{ n + 1 }} 次：{{ result.detail }}</div></details></div>
                  </div>
                </template>
              </div>
              <div v-else-if="(m.streaming && !m.reasoning) || m.phase === 'tool_call'"
                   class="bubble assistant tools">
                <ThinkingIndicator :label="waitLabel(m)" />
              </div>
            </template>
          </template>

          <!-- tool 结果：单行摘要 pill -->
          <template v-else>
            <div v-if="!(chat.messages[i - 1]?.role === 'assistant' && chat.messages[i - 1]?.toolCalls?.some(t => t.name === m.toolName))" class="tool-trace" :class="{ fail: m.ok === false }">
              <AppIcon class="tool-ic" :class="{ fail: m.ok === false }" :name="toolIconName(m.toolName ?? '')" :size="16" :label="m.toolName ?? '工具'" />
              <span class="tool-name">{{ m.toolName ?? '工具' }}</span>
              <span v-if="m.detail" class="tool-detail">{{ m.detail }}</span>
            </div>
          </template>
        </div>

        <div v-if="chat.sending && !liveMsg" class="at-thinking">
          <ThinkingIndicator label="正在分析任务" />
        </div>
      </div>

      <div class="at-foot">
        <ChatAttachTray :items="chat.pendingImages" @remove="chat.removePendingImage" />
        <div class="at-inputbar">
          <button type="button" class="at-shot" data-e2e="chat-shot-open"
                  aria-label="添加截图" title="添加截图（截取当前视野）"
                  :disabled="chat.sending || creating"
                  @click="pickerOpen = true">
            <AppIcon name="camera" :size="14" />
          </button>
          <input v-model="draft" class="at-input" type="text"
                 :placeholder="placeholder"
                 :disabled="chat.sending || creating"
                 @keyup.enter="onSend()" />
          <button type="button" class="cbtn" :disabled="!canSend()" @click="onSend()">
            {{ chat.sending || creating ? '…' : '发送' }}
          </button>
        </div>
      </div>
    </template>

    <ChatImagePicker v-if="pickerOpen" @close="pickerOpen = false" />
  </div>
</template>

<style scoped>
.at {
  position: relative;          /* 会话菜单的定位基准 */
  height: 100%;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  background: var(--surface);
}
/* 固定 30px：菜单的 `top` 按这个数落位（头部高度不随内容变） */
.at-head {
  flex: none;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 0 8px;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}
.at-sess-tools { display: flex; align-items: center; gap: 2px; }
/* 三个图标键（新会话 / 历史 / 删除当前）：24px 方形，图标本体 16px。
   图标是 `<img>` 里的 currentColor 单色 SVG，改不了颜色 —— 状态靠底色与描边表达。 */
.at-hbtn {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 1px solid transparent;
  border-radius: var(--r-ctrl);
  background: transparent;
  cursor: pointer;
  transition: border-color var(--motion-hover) var(--ease-standard), background var(--motion-hover) var(--ease-standard), opacity var(--motion-hover) var(--ease-standard);
}
.at-hbtn:hover:not(:disabled) { border-color: var(--accent-2); background: var(--accent-soft); }
.at-hbtn.on { border-color: var(--accent-2); background: var(--accent-soft); }
.at-hbtn.danger:hover:not(:disabled) { border-color: var(--err-line); background: var(--err-bg); }
.at-hbtn:disabled { opacity: .35; cursor: not-allowed; }
.at-go { font-size: 11px; color: var(--accent-ink); text-decoration: none; font-weight: 500; }
.at-go:hover { text-decoration: underline; }

/* 透明捕手：点菜单之外的任何地方即关（与设置浮层同一手法） */
.at-menu-catch { position: fixed; inset: 0; z-index: 40; }
.at-menu {
  position: absolute;
  z-index: 41;
  left: 8px;
  top: 30px;
  width: 238px;
  max-height: calc(100% - 38px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-ctrl);
  box-shadow: 0 6px 18px rgba(20, 30, 26, 0.14);
}
.at-menu-cap {
  flex: none;
  padding: 7px 10px 4px;
  font-size: 10.5px;
  font-weight: 600;
  color: var(--ink-faint);
}
.at-menu-empty { margin: 0; padding: 2px 10px 9px; font-size: 11px; color: var(--ink-faint); }
.at-sess-list {
  flex: 1;
  min-height: 0;
  overflow: auto;
  list-style: none;
  margin: 0;
  padding: 0 4px 4px;
}
.at-sess-item {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 5px 6px;
  border: none;
  border-radius: 6px;
  background: none;
  color: var(--ink-sub);
  font-size: 11.5px;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: background var(--motion-hover) var(--ease-standard), color var(--motion-hover) var(--ease-standard);
}
.at-sess-item:hover { background: var(--surface-2); }
.at-sess-item.cur { background: var(--accent-soft); color: var(--accent-ink); font-weight: 600; }
.at-sess-id { font-family: var(--font-mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.at-pin {
  margin-right: 4px;
  padding: 0 4px;
  border-radius: var(--r-pill);
  background: var(--warn-bg);
  color: var(--warn);
  font-family: inherit;
  font-size: 9.5px;
  font-weight: 600;
}
.at-sess-time { flex: none; font-size: 10.5px; color: var(--ink-faint); }

.at-err {
  flex: none; margin: 0; padding: 6px 10px; font-size: 11px; color: var(--err);
  background: var(--err-bg); border-bottom: 1px solid var(--err-line);
}

.at-disabled {
  flex: 1;
  overflow: auto;
  padding: 14px 12px;
  display: flex; flex-direction: column; gap: 8px;
  background: var(--surface);
}
.at-desc { margin: 0; font-size: 12.5px; color: var(--ink); font-weight: 600; }
.at-reason {
  margin: 0; font-size: 12px; color: var(--ink-sub); line-height: 1.7;
  background: var(--surface-2); border: 1px solid var(--line);
  border-radius: var(--r-ctrl); padding: 8px 10px;
}

.at-scroll {
  flex: 1; min-height: 0; overflow: auto;
  padding: 10px;
  display: flex; flex-direction: column; gap: 8px;
  background: var(--surface);
}
.at-empty { color: var(--ink-sub); font-size: 12px; line-height: 1.7; text-align: left; }
.at-empty p { margin: 4px 0; }
.at-hint { color: var(--ink-faint); font-size: 11px; }
.at-thinking { display: flex; justify-content: center; padding: 6px 0; }

.at-row { display: flex; flex-direction: column; }
.at-row.user { align-items: flex-end; }
.bubble {
  max-width: 94%; padding: 8px 11px; border-radius: 12px;
  font-size: 12.5px; line-height: 1.6; white-space: pre-wrap; word-break: break-word;
}
.bubble.user { background: var(--accent-grad); color: #fff; border-bottom-right-radius: 3px; }
.uq { margin: 0; }
.bubble.user.has-shots .uq { margin-top: 5px; }
/* 开发者模式附文：浅米色（复用 --warn-bg/--warn-line），默认收起 */
.ctx-dev {
  max-width: 94%; margin-top: 4px;
  background: var(--warn-bg); border: 1px solid var(--warn-line);
  border-radius: 8px; padding: 4px 8px;
}
.ctx-dev > summary {
  cursor: pointer; user-select: none;
  font-size: 10.5px; font-weight: 600; color: var(--ink-sub);
}
.ctx-dev[open] > summary { margin-bottom: 4px; }
.ctx-body {
  margin: 0; white-space: pre-wrap; word-break: break-word;
  font-family: var(--font-mono); font-size: 10px; line-height: 1.55;
  color: var(--ink-sub);
}
/* assistant 内容由 MarkdownBody 渲染成块级元素，白空格预格式化会让段落间多出空行 */
.bubble.assistant {
  max-width: 96%; white-space: normal;
  background: var(--surface-2); color: var(--ink-body);
  border: 1px solid var(--line); border-bottom-left-radius: 3px;
  align-self: flex-start;
}
.bubble.assistant.err { white-space: pre-wrap; background: var(--err-bg); color: var(--err); border-color: var(--err-line); }
.bubble.assistant.tools {
  width: 100%; max-width: 100%; box-sizing: border-box;
  background: #fff; border: 1px solid #D8E3DF; border-radius: 9px;
  box-shadow: 0 1px 2px rgba(0,0,0,.04), 0 4px 12px rgba(0,0,0,.06), 0 12px 32px rgba(0,0,0,.08);
  align-self: flex-start; padding: 12px;
}
.agent-tool-list { display: grid; gap: 6px; width: 100%; min-width: 0; }
.at-row :deep(.reason) { max-width: 96%; }
.tc-line { font-size: 11px; color: var(--ink-sub); display: inline-flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.tc-label { margin-right: 2px; }
.tc-tool { display: inline-flex; align-items: center; gap: 4px; font-family: var(--font-mono); font-weight: 600; color: var(--ink-body); }
.tool-card-head { display: flex; align-items: center; gap: 7px; font-size: 12px; }
.tool-card { width: 100%; }
.tool-card-head code { font-family: var(--font-mono); color: var(--ink-sub); }
.tool-state { margin-left: auto; color: var(--ok); }
.tool-query { margin-top: 7px; font-size: 11px; color: var(--ink-sub); }
.tool-query pre { margin: 5px 0 0; white-space: pre-wrap; }
.tool-summary { margin-top: 7px; font-size: 11px; color: var(--ink-sub); }
.tool-summary { padding: 8px 10px; background: #F5F8F6; border-radius: 7px; }
.tool-history { margin-top: 8px; font-size: 11px; color: var(--ink-sub); line-height: 1.7; }
/* 有正文时工具声明缩成正文脚注，不再单占一个虚线气泡 */
.tc-inline {
  margin-top: 6px; padding-top: 5px;
  border-top: 1px dashed var(--line);
  font-size: 11px; color: var(--ink-sub);
}

.tool-trace { width: 100%; box-sizing: border-box; align-self: flex-start; display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--ink-body); background: #F5F8F6; border: 1px solid #D8E3DF; border-radius: 9px; padding: 8px 12px; }
.tool-trace.fail { color: var(--err); background: var(--err-bg); border-color: var(--err-line); }
.tool-ic.fail { color: var(--err); }
.tool-name { font-family: var(--font-mono); font-weight: 600; flex: none; }
.tool-detail { opacity: .85; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* 托板与输入行同属底部一块：分隔线画在块顶，缩略图不悬在消息区里 */
.at-foot { flex: none; border-top: 1px solid var(--line); background: var(--surface); }
.at-inputbar {
  flex: none;
  display: flex; gap: 6px;
  padding: 8px;
  background: var(--surface);
}
.at-shot {
  flex: none; width: 30px; height: 30px;
  display: flex; align-items: center; justify-content: center;
  padding: 0;
  border: 1px solid var(--line); border-radius: var(--r-ctrl);
  background: var(--surface-2); cursor: pointer;
  transition: border-color var(--motion-hover) var(--ease-standard), background var(--motion-hover) var(--ease-standard), opacity var(--motion-hover) var(--ease-standard);
}
.at-shot:hover:not(:disabled) { border-color: var(--accent-2); background: var(--accent-soft); }
.at-shot:disabled { opacity: .5; cursor: not-allowed; }
.at-shot:focus-visible { outline: 2px solid var(--accent-3); outline-offset: 1px; }
/* 托板在窄侧舱里收紧左右内距（默认是给主聊天区留的 16px） */
.at-foot :deep(.tray) { padding: 8px 8px 0; }
.at-input {
  flex: 1; min-width: 0; height: 30px; padding: 0 10px;
  border: 1px solid var(--line); border-radius: var(--r-ctrl);
  background: var(--surface-2); font-size: 12px; font-family: inherit; color: var(--ink);
  outline: none; transition: border-color var(--motion-hover) var(--ease-standard), background var(--motion-hover) var(--ease-standard);
}
.at-input::placeholder { color: var(--ink-faint); }
.at-input:focus { border-color: var(--accent-3); background: var(--surface); }
.at-input:disabled { background: var(--surface-2); color: var(--ink-faint); }
.cbtn {
  flex: none; height: 30px; padding: 0 12px;
  border: none; border-radius: var(--r-ctrl);
  background: var(--accent-grad); color: #fff; cursor: pointer; font-size: 12px;
  font-weight: 500; font-family: inherit;
  transition: filter var(--motion-hover) var(--ease-standard);
}
.cbtn:hover { filter: brightness(1.05); }
.cbtn:disabled { opacity: .55; cursor: not-allowed; }
</style>
