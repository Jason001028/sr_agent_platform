<script setup lang="ts">
/**
 * AgentChatTab.vue — 上下文侧舱 [Agent] tab（阶段6 L2）
 * ------------------------------------------------------------------
 * - 仅盘阵场景可用（activeRec.sceneId 非空）；本地/离线 TIF 或未开图 → 禁用并给出原因。
 * - 与 /chat **同一** chat store / 会话 / 后端；完整会话管理只在 /chat（这里不 init、
 *   不建会话列表，首问才自动 apiCreateSession）。角落给「去 Chat 页继续 ↗」。
 * - 每个发送 = agentPayload(question, buildContextNote(snap)) —— question 原样 + CTX_DIVIDER
 *   + 自动上下文快照（图像名 / 原图 W×H / 显示尺寸 / 拉伸 + 选中 ROI 的确定性统计）。
 *   Agent 只解读/建议，数字一律来自 buildStats，不得编造（见 agentContext.ts 纪律）。
 * - 渲染形状与 /chat 一致（SSE 归并出的 ChatMsg）；工具回合折成单行摘要。用户气泡只显示
 *   原话：CTX_DIVIDER 之后的自动附文默认不进渲染，只有开发者模式在气泡下给一块浅米色
 *   折叠面板（`<details>`，默认收起）—— 排查「Agent 到底看到了什么」时才展开。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { useViewerStore } from '../stores/viewer';
import { useChatStore } from '../stores/chat.js';
import type { ChatMsg } from '../stores/chat.js';
import MarkdownBody from './MarkdownBody.vue';
import ReasoningBlock from './ReasoningBlock.vue';
import ThinkingIndicator from './ThinkingIndicator.vue';
import ChatAttachTray from './ChatAttachTray.vue';
import ChatImagePicker from './ChatImagePicker.vue';
import ChatBubbleImages from './ChatBubbleImages.vue';
import AppIcon from './AppIcon.vue';
import {
  agentPayload, buildContextNote, splitAgentPayload,
} from '../lib/agentContext.js';
import type { ViewerContextSnap } from '../lib/agentContext.js';
import { isDeveloperMode } from '../lib/developerMode.js';
import { roiOrigGeom } from '../lib/roiStats.js';

const viewer = useViewerStore();
const chat = useChatStore();
const draft = ref('');
const scrollEl = ref<HTMLElement | null>(null);
/** 截图选择器（截查看器当前画面，来源是左侧影像列表）。 */
const pickerOpen = ref(false);

const rec = computed(() => viewer.activeRec);
/** 仅盘阵场景可用；无图 / 本地(无 sceneId) → false 并给 reason。 */
const enabled = computed(() => Boolean(rec.value?.sceneId));
/** 无会话时首问会自动建会话 —— 输入框始终可输入；placeholder 提示差异。 */
const placeholder = computed(() =>
  chat.sessionId
    ? '问 Agent（自动附当前 ROI 统计）…'
    : '输入问题即自动新建会话并附上下文…');
const reason = computed(() => {
  const r = rec.value;
  if (!r) return '当前未打开图像。先从左侧打开一个文件，或进「打开场景」选盘阵场景。';
  if (!r.sceneId) return '本地 / 离线 TIF 无 sceneId —— Agent 只在盘阵场景（「打开场景」进入）可用，且与「任务状态」同源。';
  return '';
});

/** 当前查看器确定性上下文快照（禁用时 null；每次发送重新取，保证数字最新）。 */
const snap = computed<ViewerContextSnap | null>(() => {
  const r = rec.value;
  if (!r || !r.sceneId) return null;
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
function toolNames(m: ChatMsg): string {
  return (m.toolCalls ?? []).map((t) => t.name).join(', ') || '未知工具';
}

/** 队尾仍在流式接收的 assistant 消息（思考/回答/工具声明阶段指示用）。 */
const liveMsg = computed<ChatMsg | null>(() => {
  const last = chat.messages[chat.messages.length - 1];
  return last && last.role === 'assistant' && last.streaming ? last : null;
});

function waitLabel(m: ChatMsg): string {
  if (m.phase === 'tool_call') return '正在组织工具调用';
  if (m.phase === 'answering') return '正在生成回答';
  return '模型推理中';
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
  void chat.send(agentPayload(text, buildContextNote(s)));
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
    <!-- 头部：当前会话 + 去 Chat 页 -->
    <div class="at-head">
      <span v-if="chat.sessionId" class="at-sess" :title="chat.sessionId">
        会话 {{ shortId(chat.sessionId) }}
      </span>
      <span v-else class="at-sess empty">未开始会话</span>
      <router-link class="at-go" to="/chat">去 Chat 页继续 ↗</router-link>
    </div>

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
          <p class="at-hint">Agent 只解读画面上的确定性数字；涉及具体区域建议先在 ROI / 工具 tab 框选。</p>
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
                              :streaming="m.streaming" :phase="m.phase ?? null" />
              <div v-if="m.content" class="bubble assistant">
                <MarkdownBody :source="m.content" />
                <div v-if="m.toolCalls && m.toolCalls.length" class="tc-inline">
                  🧩 Agent 调用：{{ toolNames(m) }}
                </div>
              </div>
              <div v-else-if="m.toolCalls && m.toolCalls.length" class="bubble assistant tools">
                <span class="tc-line">🧩 Agent 调用：{{ toolNames(m) }}</span>
              </div>
              <div v-else-if="(m.streaming && !m.reasoning) || m.phase === 'tool_call'"
                   class="bubble assistant tools">
                <ThinkingIndicator :label="waitLabel(m)" />
              </div>
            </template>
          </template>

          <!-- tool 结果：单行摘要 pill -->
          <template v-else>
            <div class="tool-trace" :class="{ fail: m.ok === false }">
              <span class="tool-ic" :class="{ fail: m.ok === false }">{{ m.ok === false ? '✗' : (m.ok === true ? '✓' : '…') }}</span>
              <span class="tool-name">{{ m.toolName ?? '工具' }}</span>
              <span v-if="m.detail" class="tool-detail">{{ m.detail }}</span>
            </div>
          </template>
        </div>

        <div v-if="chat.sending && !liveMsg" class="at-thinking">
          <ThinkingIndicator label="模型推理中" />
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
  height: 100%;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  background: var(--surface);
}
.at-head {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 7px 10px 6px;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}
.at-sess { font-size: 11px; color: var(--ink-sub); font-family: var(--font-mono); }
.at-sess.empty { color: var(--ink-faint); }
.at-go { font-size: 11px; color: var(--accent-ink); text-decoration: none; font-weight: 500; }
.at-go:hover { text-decoration: underline; }

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
  background: #fbfcfa; border: 1px dashed var(--line); border-radius: 10px;
  align-self: flex-start; padding: 5px 10px; max-width: 94%;
}
.at-row :deep(.reason) { max-width: 96%; }
.tc-line { font-size: 11px; color: var(--ink-sub); }
/* 有正文时工具声明缩成正文脚注，不再单占一个虚线气泡 */
.tc-inline {
  margin-top: 6px; padding-top: 5px;
  border-top: 1px dashed var(--line);
  font-size: 11px; color: var(--ink-sub);
}

.tool-trace {
  align-self: flex-start; display: inline-flex; align-items: center; gap: 6px;
  font-size: 11px; color: var(--ok); background: var(--ok-bg);
  border: 1px solid var(--ok-line); border-radius: var(--r-pill); padding: 2px 10px;
  max-width: 96%; overflow: hidden;
}
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
