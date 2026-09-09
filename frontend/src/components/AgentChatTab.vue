<script setup lang="ts">
/**
 * AgentChatTab.vue — 上下文侧舱 [Agent] tab（阶段6 L2）
 * ------------------------------------------------------------------
 * - 仅盘阵场景可用（activeRec.sceneId 非空）；本地/离线 TIF 或未开图 → 禁用并给出原因。
 * - 与 /chat **同一** chat store / 会话 / 后端；完整会话管理只在 /chat（这里不 init、
 *   不建会话列表，首问才自动 apiCreateSession）。角落给「去 Chat 页继续 ↗」。
 * - 每个发送 = agentPayload(question, buildContextNote(snap)) —— question 原样 + CTX_DIVIDER
 *   + 自动上下文快照（scene_id / 原图 W×H / 显示尺寸 / 拉伸 + 选中 ROI 的确定性统计）。
 *   Agent 只解读/建议，数字一律来自 buildStats，不得编造（见 agentContext.ts 纪律）。
 * - 渲染形状与 /chat 一致（SSE 归并出的 ChatMsg）；工具回合折成单行摘要；消息气泡内把
 *   「用户原话」与「自动附上下文」用 CTX_DIVIDER 视觉分开（上下文部分缩小、半透明）。
 */
import { computed, nextTick, ref, watch } from 'vue';
import { useViewerStore } from '../stores/viewer';
import { useChatStore } from '../stores/chat.js';
import type { ChatMsg } from '../stores/chat.js';
import {
  CTX_DIVIDER, agentPayload, buildContextNote,
} from '../lib/agentContext.js';
import type { ViewerContextSnap } from '../lib/agentContext.js';
import { roiOrigGeom } from '../lib/roiStats.js';

const viewer = useViewerStore();
const chat = useChatStore();
const draft = ref('');
const scrollEl = ref<HTMLElement | null>(null);

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
  const stretch = r.route === 'jpg'
    ? '2% 线性（盘阵烘焙）'
    : (STRETCH_LABEL[viewer.stretchMode] ?? viewer.stretchMode);
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
/** 用户消息按 CTX_DIVIDER 切：上面是原话，下面是自动附的只读上下文。 */
function parts(content: string): { q: string; note: string } {
  const i = content.indexOf(CTX_DIVIDER);
  if (i < 0) return { q: content, note: '' };
  return { q: content.slice(0, i).replace(/\s+$/, ''), note: content.slice(i + CTX_DIVIDER.length).trim() };
}
function toolNames(m: ChatMsg): string {
  return (m.toolCalls ?? []).map((t) => t.name).join(', ') || '未知工具';
}

const creating = ref(false);   // 防止双击并发 newSession 建出两个会话
async function onSend(): Promise<void> {
  const text = draft.value.trim();
  const s = snap.value;
  if (!text || !enabled.value || !s || chat.sending || creating.value) return;
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

async function scrollBottom(): Promise<void> {
  await nextTick();
  if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight;
}
watch(() => chat.messages.length, () => void scrollBottom());
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
      <p class="at-desc">Agent（LLM 解读 / 建议）仅对盘阵场景开放。</p>
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
          <!-- user -->
          <template v-if="m.role === 'user'">
            <div class="bubble user">
              <!-- 原话；有自动附文时在其下缩小显示（无分隔 = 历史 /chat 消息原样） -->
              <p class="uq">{{ parts(m.content).q }}</p>
              <p v-if="parts(m.content).note" class="ctx">{{ parts(m.content).note }}</p>
            </div>
          </template>

          <!-- assistant：内容气泡 / 工具声明占位（折单行） / 思考中 -->
          <template v-else-if="m.role === 'assistant'">
            <div v-if="m.isError" class="bubble assistant err">{{ m.content }}</div>
            <div v-else-if="m.content" class="bubble assistant">{{ m.content }}</div>
            <div v-else-if="m.toolCalls && m.toolCalls.length" class="bubble assistant tools">
              <span class="tc-line">🧩 Agent 调用：{{ toolNames(m) }}</span>
            </div>
            <div v-else-if="chat.sending" class="bubble assistant tools">
              <span class="tc-line">正在思考…</span>
            </div>
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

        <p v-if="chat.sending" class="at-thinking">正在思考…</p>
      </div>

      <div class="at-inputbar">
        <input v-model="draft" class="at-input" type="text"
               :placeholder="placeholder"
               :disabled="chat.sending || creating"
               @keyup.enter="onSend()" />
        <button type="button" class="cbtn" :disabled="chat.sending || creating || !draft.trim()" @click="onSend()">
          {{ chat.sending || creating ? '…' : '发送' }}
        </button>
      </div>
    </template>
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
.at-thinking { color: var(--ink-sub); font-size: 11px; text-align: center; }

.at-row { display: flex; flex-direction: column; }
.at-row.user { align-items: flex-end; }
.bubble {
  max-width: 94%; padding: 8px 11px; border-radius: 12px;
  font-size: 12.5px; line-height: 1.6; white-space: pre-wrap; word-break: break-word;
}
.bubble.user { background: var(--accent-grad); color: #fff; border-bottom-right-radius: 3px; }
.uq { margin: 0; }
.ctx {
  margin: 5px 0 0; font-size: 10.5px; line-height: 1.55;
  color: rgba(255, 255, 255, 0.82);
  border-top: 1px dashed rgba(255, 255, 255, 0.4);
  padding-top: 5px;
  white-space: pre-wrap;
}
.bubble.assistant { background: var(--surface-2); color: var(--ink-body); border-bottom-left-radius: 3px; align-self: flex-start; }
.bubble.assistant.err { background: var(--err-bg); color: var(--err); }
.bubble.assistant.tools {
  background: #fbfcfa; border: 1px dashed var(--line); border-radius: 10px;
  align-self: flex-start; padding: 5px 10px; max-width: 94%;
}
.tc-line { font-size: 11px; color: var(--ink-sub); }

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

.at-inputbar {
  flex: none;
  display: flex; gap: 6px;
  padding: 8px;
  border-top: 1px solid var(--line);
  background: var(--surface);
}
.at-input {
  flex: 1; min-width: 0; height: 30px; padding: 0 10px;
  border: 1px solid var(--line); border-radius: var(--r-ctrl);
  background: var(--surface-2); font-size: 12px; font-family: inherit; color: var(--ink);
  outline: none; transition: border-color 0.15s ease, background 0.15s ease;
}
.at-input::placeholder { color: var(--ink-faint); }
.at-input:focus { border-color: var(--accent-3); background: var(--surface); }
.at-input:disabled { background: var(--surface-2); color: var(--ink-faint); }
.cbtn {
  flex: none; height: 30px; padding: 0 12px;
  border: none; border-radius: var(--r-ctrl);
  background: var(--accent-grad); color: #fff; cursor: pointer; font-size: 12px;
  font-weight: 500; font-family: inherit;
  transition: filter 0.15s ease;
}
.cbtn:hover { filter: brightness(1.05); }
.cbtn:disabled { opacity: .55; cursor: not-allowed; }
</style>
