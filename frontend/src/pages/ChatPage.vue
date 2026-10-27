<script setup lang="ts">
/**
 * ChatPage.vue — Agent 聊天（阶段5，api-contract.md §3.2）
 * 布局：左侧会话列表（新建/切换），右侧消息 timeline + 发送框。
 * 消息 = SSE 逐帧归并（reduceChatSse）或刷新后 GET messages 投影，同一形状：
 *   工具回合渲染为 用户 ─ Assistant(声明工具) ─ 工具结果 ─ 最终回复。
 * mock LLM（SR_LLM_MOCK=1）每回合固定 search_scenes → 总结，可离线走通。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { useChatStore } from '../stores/chat.js';
import { useViewerStore } from '../stores/viewer.js';
import { isDeveloperMode } from '../lib/developerMode.js';
import type { ChatMsg } from '../stores/chat.js';
import type { ChatSession } from '../lib/api.js';
import TokenUsagePanel from '../components/TokenUsagePanel.vue';
import MarkdownBody from '../components/MarkdownBody.vue';
import ReasoningBlock from '../components/ReasoningBlock.vue';
import { phaseLabel } from '../lib/executionOverview.js';
import ThinkingIndicator from '../components/ThinkingIndicator.vue';
import ChatAttachTray from '../components/ChatAttachTray.vue';
import GoalClarifications from '../components/GoalClarifications.vue';
import ChatImagePicker from '../components/ChatImagePicker.vue';
import ChatBubbleImages from '../components/ChatBubbleImages.vue';
import AppIcon from '../components/AppIcon.vue';
import SceneSearchResultCard from '../components/SceneSearchResultCard.vue';
import { useScenesStore } from '../stores/scenes.js';
import AgentBotIcon from '../components/AgentBotIcon.vue';
import { apiListPlugins } from '../lib/api.js';
import { loadSrConfig } from '../lib/scene.js';
import { splitAgentPayload } from '../lib/agentContext.js';

const chat = useChatStore();
const viewer = useViewerStore();
const developerMode = ref(false);
onMounted(() => { developerMode.value = isDeveloperMode(); });
watch(() => viewer.settingsOpen, (open) => {
  if (!open) developerMode.value = isDeveloperMode();
});
async function rename(s: ChatSession): Promise<void> {
  const title = window.prompt('会话标题（1—30个字符）', s.metadata?.title ?? '');
  if (title !== null) await chat.renameSession(s.session_id, title);
}
const scenes = useScenesStore();
const draft = ref('');
const tokenPluginEnabled = ref(true);
/** 截图选择器（从左侧影像列表挑一张）。 */
const pickerOpen = ref(false);
async function openSearchScene(row: Record<string, unknown>): Promise<void> {
  const name = String(row.name ?? row.sceneId ?? row.id ?? '').trim();
  if (name) await scenes.openByName(name);
}

const scrollEl = ref<HTMLElement | null>(null);

function shortId(sid: string): string {
  return sid.length > 10 ? sid.slice(0, 8) + '…' : sid;
}

function argsText(a: Record<string, unknown>): string {
  const s = JSON.stringify(a);
  return s && s !== '{}' ? s : '（无参数）';
}

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
  for (let j = index + 1; j < chat.messages.length && chat.messages[j].role === 'tool'; j++) {
    if (chat.messages[j].toolName === name) out.push(chat.messages[j]);
  }
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

/** 用户气泡只显示用户原话；查看器 Agent 页签自动附的上下文不算用户输入。 */
function userText(content: string): string {
  return splitAgentPayload(content).question;
}

function msgKey(m: ChatMsg, i: number): string {
  return m.seq !== null ? 's' + m.seq : 'x' + i;
}

/** 有截图时可以只发图（话术留空）：交给多模态模型看图，文字由它自己描述。 */
function canSend(): boolean {
  return !chat.sending && chat.hasSession()
    && (Boolean(draft.value.trim()) || chat.pendingImages.length > 0);
}

function onSend(): void {
  if (!canSend()) return;
  const text = draft.value.trim();
  draft.value = '';
  void chat.send(text);
}

/** 输入区相机键：打开影像选择器（截图来源是查看器的影像列表）。 */
function openPicker(): void {
  pickerOpen.value = true;
}

function onFeedback(rating: 'positive' | 'negative'): void {
  void chat.submitFeedback(rating);
}

/* ---------------- 会话多选 / 置顶 / 删除 ---------------- */
const selectMode = ref(false);
const selected = ref<string[]>([]);

const pickedSessions = computed(() =>
  chat.sessions.filter((s) => selected.value.includes(s.session_id)));
/** 选中项全部已置顶时，批量按钮变成「取消置顶」。 */
const allPickedPinned = computed(() =>
  pickedSessions.value.length > 0 && pickedSessions.value.every((s) => s.pinned));
const allPicked = computed(() =>
  chat.sessions.length > 0 && selected.value.length === chat.sessions.length);

function fmtTime(ts: number): string {
  const d = new Date(ts * 1000);
  const pad = (n: number): string => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function toggleSelectMode(): void {
  selectMode.value = !selectMode.value;
  if (!selectMode.value) selected.value = [];
}

function togglePick(sid: string): void {
  selected.value = selected.value.includes(sid)
    ? selected.value.filter((id) => id !== sid)
    : [...selected.value, sid];
}

function togglePickAll(): void {
  selected.value = allPicked.value ? [] : chat.sessions.map((s) => s.session_id);
}

async function togglePin(s: ChatSession): Promise<void> {
  await chat.pinSession(s.session_id, !s.pinned);
}

async function removeOne(s: ChatSession): Promise<void> {
  if (chat.sending) return;
  const ok = window.confirm(
    `删除会话 ${shortId(s.session_id)}？\n消息、用量和轨迹记录会一并删除，且不可恢复。`);
  if (!ok) return;
  await chat.deleteSession(s.session_id);
}

async function batchPin(): Promise<void> {
  const ids = selected.value.slice();
  if (!ids.length) return;
  await chat.pinSessions(ids, !allPickedPinned.value);
}

async function batchDelete(): Promise<void> {
  const ids = selected.value.slice();
  if (!ids.length || chat.sending) return;
  const ok = window.confirm(
    `删除选中的 ${ids.length} 个会话？\n消息、用量和轨迹记录会一并删除，且不可恢复。`);
  if (!ok) return;
  await chat.deleteSessions(ids);
  // 失败的会话仍在列表里，保留其勾选；全部成功则退出多选。
  selected.value = ids.filter((id) => chat.sessions.some((s) => s.session_id === id));
  if (!selected.value.length) selectMode.value = false;
}

function nearBottom(): boolean {
  const el = scrollEl.value;
  return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 80;
}

async function scrollBottom(): Promise<void> {
  await nextTick();
  if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight;
}

watch(() => chat.messages.length, () => void scrollBottom());
// 流式增量不新增消息，只改长度：已经贴着底部才跟随，用户翻上去就不拽回来。
watch(() => chat.streamTick, () => { if (nearBottom()) void scrollBottom(); });
onMounted(() => {
  void chat.init();
  void (async () => {
    try {
      const list = await apiListPlugins(loadSrConfig(), 'local');
      const plugin = list.find((p) => p.plugin_id === 'token_visualizer');
      if (plugin) tokenPluginEnabled.value = plugin.actual_state === 'enabled'
        && plugin.desired_state === 'enabled';
    } catch {
      // Keep the panel visible when an older backend has no plugin catalog yet.
    }
  })();
  void scrollBottom();
});
</script>

<template>
  <div class="chat-page">
    <!-- 会话侧栏 -->
    <aside class="cp-side">
      <button type="button" class="btn" :disabled="chat.sending" @click="chat.newSession()">
        ＋ 新建会话
      </button>

      <div class="cp-side-head">
        <span class="cp-side-title">会话</span>
        <div class="cp-side-tools">
          <button v-if="selectMode" type="button" class="cp-link-btn"
                  :disabled="!chat.sessions.length" @click="togglePickAll">
            {{ allPicked ? '全不选' : '全选' }}
          </button>
          <button type="button" class="cp-link-btn"
                  :disabled="chat.sending || !chat.sessions.length"
                  @click="toggleSelectMode">
            {{ selectMode ? '完成' : '多选' }}
          </button>
        </div>
      </div>

      <ul class="cp-sessions">
        <li v-for="s in chat.sessions" :key="s.session_id"
            class="cp-sess-item"
            :class="{ cur: s.session_id === chat.sessionId,
                      picked: selected.includes(s.session_id) }">
          <input v-if="selectMode" type="checkbox" class="cp-check"
                 :checked="selected.includes(s.session_id)"
                 :aria-label="'选择会话 ' + shortId(s.session_id)"
                 @change="togglePick(s.session_id)" />
          <button type="button" class="cp-sess-btn"
                  :disabled="chat.sending"
                  :title="'更新时间 ' + new Date(s.updated_at * 1000).toLocaleString()"
                  @click="selectMode ? togglePick(s.session_id) : chat.selectSession(s.session_id)">
            <span class="cp-sess-main">
              <span v-if="s.pinned" class="cp-pin-tag" title="已置顶">置顶</span>
              <span class="cp-sess-id" :title="s.session_id">{{ s.metadata?.title || shortId(s.session_id) }}</span>
            </span>
            <span class="cp-sess-time">{{ fmtTime(s.updated_at) }}</span>
          </button>
          <span v-if="!selectMode" class="cp-sess-actions">
            <button type="button" class="cp-act" @click.stop="rename(s)">重命名</button>
            <button type="button" class="cp-act" :disabled="chat.sending"
                    @click.stop="togglePin(s)">{{ s.pinned ? '取消置顶' : '置顶' }}</button>
            <button type="button" class="cp-act danger" :disabled="chat.sending"
                    @click.stop="removeOne(s)">删除</button>
          </span>
        </li>
      </ul>

      <div v-if="selectMode" class="cp-batchbar">
        <span class="cp-batch-count">已选 {{ selected.length }}</span>
        <button type="button" class="cp-batch-btn" :disabled="!selected.length"
                @click="batchPin">{{ allPickedPinned ? '取消置顶' : '置顶' }}</button>
        <button type="button" class="cp-batch-btn danger"
                :disabled="!selected.length || chat.sending"
                @click="batchDelete">删除</button>
      </div>

      <p v-if="!chat.loading && !chat.sessions.length" class="cp-side-hint">
        暂无会话，点「新建会话」开始。
      </p>
    </aside>

    <!-- 对话区 -->
    <section class="cp-main">
      <div class="cp-head">
        <span class="cp-title"><AgentBotIcon :status="chat.agentStatus" :size="16" />Agent 聊天</span>
        <span v-if="chat.sessionId" class="cp-sessid">{{ chat.sessions.find(s => s.session_id === chat.sessionId)?.metadata?.title || shortId(chat.sessionId) }}</span>
        <span v-else class="cp-sessid empty">未选择会话</span>
        <TokenUsagePanel v-if="tokenPluginEnabled" :usage="chat.usage" />
      </div>

      <p v-if="chat.error" class="cp-err">{{ chat.error }}</p>

      <div ref="scrollEl" class="cp-scroll">
        <div v-if="chat.loading" class="cp-loading">
          <ThinkingIndicator label="加载会话中" />
        </div>
        <div v-else-if="!chat.messages.length" class="cp-empty">
          <p>离线试跑提示：当前运行模式（SR_LLM_MOCK / SR_SLURM_FAKE / 盘阵）由服务端 env
            决定，聊天可发任意话术验证链路。</p>
        </div>

        <div v-for="(m, i) in chat.messages" :key="msgKey(m, i)" class="cp-row" :class="m.role"
             v-show="m.toolName !== 'request_clarification' && !(m.role === 'assistant' && !m.content && m.toolCalls?.length === 1 && m.toolCalls[0].name === 'request_clarification')">
          <!-- user：截图在上，原话在下；纯图消息不渲染空段落 -->
          <template v-if="m.role === 'user'">
            <div class="bubble user" :class="{ 'has-shots': Boolean(m.images?.length) }">
              <ChatBubbleImages v-if="m.images?.length" :images="m.images" />
              <p v-if="userText(m.content)" class="utext">{{ userText(m.content) }}</p>
            </div>
          </template>

          <!-- assistant：推理折叠块 + 正文；工具声明并入同一条消息（与 history 投影一致） -->
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
              <div v-else-if="m.toolCalls && m.toolCalls.length" class="bubble assistant tools">
                <div v-for="tc in m.toolCalls" :key="tc.id || tc.name" class="tool-card">
                  <div class="tool-card-head"><AppIcon :name="toolIconName(tc.name)" :size="17" :label="tc.name" /><strong>{{ tc.name === 'search_scenes' ? '检索目标场景' : tc.name }}</strong><code>{{ tc.name }}</code><span v-if="toolResultsAt(i, tc.name).length > 1" class="tool-state">共尝试 {{ toolResultsAt(i, tc.name).length }} 次</span><span v-else class="tool-state">{{ toolResultAt(i, tc.name)?.ok === true ? '✓ 完成' : toolResultAt(i, tc.name)?.ok === false ? '× 失败' : chat.sending ? '⏳ 调用中' : '结果未确认' }}</span></div>
                  <details class="tool-query"><summary>查询条件</summary><pre>{{ argsText(tc.args) }}</pre></details>
                  <div v-if="toolResultsAt(i, tc.name).length" class="tool-summary">最终结果：{{ toolResultsAt(i, tc.name).at(-1)?.detail }}</div>
                  <SceneSearchResultCard v-if="tc.name === 'search_scenes'" :data="toolResultAt(i, tc.name)?.data" :args="tc.args" :status="toolResultAt(i, tc.name)?.ok === false ? 'failed' : toolResultAt(i, tc.name)?.ok === true ? 'completed' : 'running'" @open="openSearchScene" />
                  <details v-if="toolResultsAt(i, tc.name).length > 1" class="tool-history"><summary>查看 {{ toolResultsAt(i, tc.name).length }} 次调用记录</summary><div v-for="(result, n) in toolResultsAt(i, tc.name)" :key="n">第 {{ n + 1 }} 次：{{ result.detail }}</div></details>
                </div>
              </div>
              <div v-else-if="(m.streaming && !m.reasoning) || m.phase === 'tool_call'"
                   class="bubble assistant tools">
                <ThinkingIndicator :label="waitLabel(m)" />
              </div>
            </template>
          </template>

          <!-- tool 结果行 -->
          <template v-else>
            <div v-if="!(chat.messages[i - 1]?.role === 'assistant' && chat.messages[i - 1]?.toolCalls?.some(t => t.name === m.toolName))" class="tool-trace" :class="{ fail: m.ok === false }"><AppIcon :name="toolIconName(m.toolName ?? '')" :size="16" /><span class="tool-name">{{ m.toolName ?? '工具' }}</span><span v-if="m.detail" class="tool-detail">{{ m.detail }}</span></div>
          </template>
        </div>

        <GoalClarifications :session-id="chat.sessionId" @updated="!chat.sending && chat.sessionId && chat.loadHistory(chat.sessionId)" />
        <div v-if="chat.sending && !liveMsg" class="cp-thinking">
          <ThinkingIndicator label="正在分析任务" />
        </div>
        <div v-if="chat.lastRunId && !chat.sending" class="cp-feedback">
          <span>这次回答有帮助吗？</span>
          <button type="button" class="cp-feedback-btn"
                  :class="{ selected: chat.feedbackRating === 'positive' }"
                  :disabled="Boolean(chat.feedbackRating)"
                  @click="onFeedback('positive')">有帮助</button>
          <button type="button" class="cp-feedback-btn"
                  :class="{ selected: chat.feedbackRating === 'negative' }"
                  :disabled="Boolean(chat.feedbackRating)"
                  @click="onFeedback('negative')">需改进</button>
        </div>
      </div>

      <div class="cp-foot">
        <ChatAttachTray :items="chat.pendingImages" @remove="chat.removePendingImage" />
        <div class="cp-inputbar">
          <button type="button" class="cp-shot" data-e2e="chat-shot-open"
                  aria-label="添加截图" title="添加截图（截取查看器当前画面）"
                  :disabled="chat.sending"
                  @click="openPicker">
            <AppIcon name="camera" :size="16" />
          </button>
          <input v-model="draft" class="cp-input" type="text"
                 placeholder="给 Agent 下指令（离线 mock 每回合固定检索盘阵场景）…"
                 :disabled="chat.sending || !chat.hasSession()"
                 @keyup.enter="onSend()" />
          <button type="button" class="btn" :disabled="!canSend()"
                  @click="onSend()">
            {{ chat.sending ? '处理中…' : '发送' }}
          </button>
        </div>
      </div>
    </section>

    <ChatImagePicker v-if="pickerOpen" @close="pickerOpen = false" />
  </div>
</template>

<style scoped>
.chat-page {
  height: calc(100vh - var(--topbar-h));
  display: flex;
  gap: 14px;
  max-width: var(--page-w);
  margin: 0 auto;
  padding: 14px 20px 16px;
  box-sizing: border-box;
}
.cp-side {
  width: 232px;
  flex: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-panel);
  box-shadow: var(--shadow-card);
  padding: 12px;
  overflow: hidden;
}
.cp-side-head { display: flex; align-items: center; justify-content: space-between; padding: 0 2px; }
.cp-side-title { font-size: 12px; font-weight: 600; color: var(--ink-sub); }
.cp-side-tools { display: flex; align-items: center; gap: 2px; }
.cp-link-btn {
  border: none; background: transparent; color: var(--accent-ink);
  font-size: 11.5px; font-family: inherit; padding: 2px 5px;
  border-radius: 6px; cursor: pointer;
}
.cp-link-btn:hover:not(:disabled) { background: var(--accent-soft); color: var(--accent-deep); }
.cp-link-btn:disabled { color: var(--ink-faint); cursor: not-allowed; }

.cp-sessions {
  list-style: none; margin: 0; padding: 0 2px 0 0;
  flex: 1; min-height: 0; overflow: auto;
  display: flex; flex-direction: column; gap: 3px;
}
.cp-sess-item { position: relative; display: flex; align-items: center; gap: 5px; }
.cp-check { flex: none; accent-color: var(--accent-3); cursor: pointer; }
.cp-sessions li.cur .cp-sess-btn {
  background: var(--accent-soft);
  color: var(--accent-deep);
  border-color: rgba(61, 169, 164, 0.35);
  font-weight: 600;
}
.cp-sess-item.picked .cp-sess-btn {
  background: var(--accent-soft);
  border-color: rgba(61, 169, 164, 0.35);
}
.cp-sess-btn {
  flex: 1; min-width: 0; display: flex; flex-direction: column;
  align-items: flex-start; gap: 1px;
  text-align: left; padding: 6px 9px; font-size: 13px;
  border: 1px solid transparent; border-radius: var(--r-ctrl);
  background: transparent;
  color: var(--ink-body); cursor: pointer; font-family: inherit;
  transition: background var(--motion-hover) var(--ease-standard);
}
.cp-sess-btn:hover:not(:disabled) { background: var(--surface-2); }
.cp-sess-btn:disabled { opacity: .6; cursor: not-allowed; }
.cp-sess-main { display: flex; align-items: center; gap: 5px; max-width: 100%; }
.cp-sess-id {
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-family: var(--font-mono); font-size: 12.5px;
}
.cp-sess-time { font-size: 10.5px; color: var(--ink-faint); font-weight: 400; }
.cp-pin-tag {
  flex: none; font-size: 10px; line-height: 1; font-weight: 500;
  color: var(--accent-deep); background: var(--accent-soft);
  border-radius: 4px; padding: 2px 4px;
}
/* 行内操作只在悬停/聚焦时露出，平时让位给会话名与时间 */
.cp-sess-actions {
  position: absolute; right: 4px; top: 50%; transform: translateY(-50%);
  display: flex; gap: 2px; padding-left: 14px;
  background: linear-gradient(90deg, transparent, var(--surface) 22%);
  opacity: 0; pointer-events: none; transition: opacity var(--motion-fade) var(--ease-standard);
}
.cp-sess-item.cur .cp-sess-actions {
  background: linear-gradient(90deg, transparent, var(--accent-soft) 22%);
}
.cp-sess-item:hover .cp-sess-actions,
.cp-sess-item:focus-within .cp-sess-actions { opacity: 1; pointer-events: auto; }
.cp-act {
  border: none; background: transparent; color: var(--ink-sub);
  font-size: 11px; font-family: inherit; padding: 2px 5px;
  border-radius: 5px; cursor: pointer; white-space: nowrap;
}
.cp-act:hover:not(:disabled) { background: var(--surface-2); color: var(--accent-deep); }
.cp-act.danger:hover:not(:disabled) { background: var(--err-bg); color: var(--err); }
.cp-act:disabled { opacity: .5; cursor: not-allowed; }

.cp-batchbar {
  flex: none; display: flex; align-items: center; gap: 6px;
  padding: 7px 8px; border: 1px solid var(--line);
  border-radius: var(--r-ctrl); background: var(--surface-2);
}
.cp-batch-count { flex: 1; font-size: 11.5px; color: var(--ink-sub); }
.cp-batch-btn {
  border: 1px solid var(--line); border-radius: var(--r-pill);
  background: var(--surface); color: var(--ink-body);
  font-size: 11.5px; font-family: inherit; padding: 3px 9px; cursor: pointer;
}
.cp-batch-btn:hover:not(:disabled) {
  border-color: var(--accent-3); color: var(--accent-deep); background: var(--accent-soft);
}
.cp-batch-btn.danger { color: var(--err); }
.cp-batch-btn.danger:hover:not(:disabled) {
  border-color: var(--err-line); color: var(--err); background: var(--err-bg);
}
.cp-batch-btn:disabled { opacity: .5; cursor: not-allowed; }

.cp-side-hint { flex: none; color: var(--ink-sub); font-size: 12px; line-height: 1.7; }
.cp-side .btn { width: 100%; }

.cp-main {
  flex: 1; min-width: 0;
  display: flex; flex-direction: column;
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-panel);
  box-shadow: var(--shadow-card);
  overflow: hidden;
}
.cp-head {
  display: flex; align-items: center; flex-wrap: wrap; gap: 10px;
  padding: 12px 18px; border-bottom: 1px solid var(--line); flex: none;
  background: var(--surface);
}
.cp-title { display: inline-flex; align-items: center; gap: 6px; font-size: 16px; font-weight: 600; color: var(--ink); }
.cp-sessid { font-size: 12px; color: var(--ink-sub); font-family: var(--font-mono); }
.cp-sessid.empty { color: var(--ink-faint); }
.cp-err {
  margin: 0; padding: 7px 18px; font-size: 13px; color: var(--err);
  background: var(--err-bg); border-bottom: 1px solid var(--err-line);
}

.cp-scroll { flex: 1; overflow: auto; padding: 18px; display: flex; flex-direction: column; gap: 10px; background: var(--surface); }
.cp-empty { color: var(--ink-sub); font-size: 13px; text-align: center; }
.cp-loading, .cp-thinking { display: flex; justify-content: center; padding: 12px 0; }
.cp-feedback { display: flex; align-items: center; justify-content: center; gap: 8px; color: var(--ink-sub); font-size: 12px; }
.cp-feedback-btn { border: 1px solid var(--line); border-radius: var(--r-pill); background: var(--surface); color: var(--ink-sub); padding: 3px 10px; cursor: pointer; }
.cp-feedback-btn:hover:not(:disabled), .cp-feedback-btn.selected { border-color: var(--accent-3); color: var(--accent-deep); background: var(--accent-soft); }
.cp-feedback-btn:disabled { cursor: default; opacity: .75; }
.cp-empty p { max-width: 520px; margin: 30px auto 0; line-height: 1.7; }

.cp-row { display: flex; flex-direction: column; }
.cp-row.user { align-items: flex-end; }
.bubble {
  max-width: 78%; padding: 9px 14px; border-radius: 14px;
  font-size: 13.5px; line-height: 1.6; white-space: pre-wrap; word-break: break-word;
}
.bubble.user { background: var(--accent-grad); color: #fff; border-bottom-right-radius: 4px; box-shadow: 0 2px 6px rgba(45,164,162,0.18); }
/* 原话段落自己吃掉 margin：气泡内距已给足，浏览器默认的 p 间距会把它顶偏 */
.bubble.user .utext { margin: 0; }
/* 图在上、话在下，两者之间留一点缝 */
.bubble.user.has-shots .utext { margin-top: 6px; }
/* assistant 内容由 MarkdownBody 渲染成块级元素，白空格预格式化会让段落间多出空行 */
.bubble.assistant {
  max-width: 86%; white-space: normal;
  background: var(--surface-2); color: var(--ink-body);
  border: 1px solid var(--line); border-bottom-left-radius: 4px;
}
.bubble.assistant.err { white-space: pre-wrap; background: var(--err-bg); color: var(--err); border-color: var(--err-line); }
.bubble.tools { background: #fbfcfa; border: 1px dashed var(--line); border-radius: 12px; }
.bubble.assistant.tools { width: 100%; max-width: 100%; box-sizing: border-box; padding: 12px; background: #fff; border: 1px solid #D8E3DF; border-radius: 9px; box-shadow: 0 1px 2px rgba(0,0,0,.04), 0 4px 12px rgba(0,0,0,.06), 0 12px 32px rgba(0,0,0,.08); }
.tool-title { margin: 0 0 4px; font-size: 12px; color: var(--ink-sub); }
.tool-decl { display: flex; gap: 8px; align-items: baseline; font-size: 13px; }
.tool-decl code { color: var(--accent-deep); font-family: var(--font-mono); }
.tool-args { color: var(--ink-sub); font-size: 12px; word-break: break-all; }
/* 推理块与正文气泡同宽，避免同一回合出现两种右边距 */
.cp-row :deep(.reason) { max-width: 86%; }
/* 有正文时不再单独占一条「工具声明」气泡，缩成正文脚注（工具轮的主线仍是同一条 assistant） */
.tc-inline {
  margin-top: 7px; padding-top: 6px;
  border-top: 1px dashed var(--line);
  color: var(--ink-sub); font-size: 11.5px;
}
.tc-inline { display: inline-flex; align-items: center; gap: 7px; flex-wrap: wrap; }
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

.tool-trace {
  width: 100%; box-sizing: border-box; align-self: flex-start; display: flex; align-items: center; gap: 7px;
  font-size: 12px; color: var(--ink-body); background: #F5F8F6;
  border: 1px solid #D8E3DF; border-radius: 9px; padding: 8px 12px;
}
.tool-trace.fail { color: var(--err); background: var(--err-bg); border-color: var(--err-line); }
.tool-ic.fail { color: var(--err); }
.tool-name { font-family: var(--font-mono); font-weight: 600; }
.tool-detail { opacity: .85; }

/* 输入区连托板一起做成一整块：分隔线画在整块顶部，待发缩略图才不像悬在消息区里 */
.cp-foot { flex: none; border-top: 1px solid var(--line); background: var(--surface); }
.cp-inputbar {
  display: flex; gap: 10px; padding: 12px 16px;
  flex: none;
  background: var(--surface);
}
/* 相机键：与输入框同高、中性面，不抢「发送」那颗主按钮的视觉重量 */
.cp-shot {
  flex: none; width: 36px; height: 36px;
  display: flex; align-items: center; justify-content: center;
  padding: 0;
  border: 1px solid var(--line); border-radius: var(--r-ctrl);
  background: var(--surface-2);
  cursor: pointer;
  transition: border-color var(--motion-hover) var(--ease-standard), background var(--motion-hover) var(--ease-standard), opacity var(--motion-hover) var(--ease-standard);
}
.cp-shot:hover:not(:disabled) {
  border-color: var(--accent-2); background: var(--accent-soft);
}
.cp-shot:disabled { opacity: .5; cursor: not-allowed; }
.cp-shot:focus-visible { outline: 2px solid var(--accent-3); outline-offset: 1px; }
.cp-input {
  flex: 1; height: 36px; padding: 0 12px; border: 1px solid var(--line);
  border-radius: var(--r-ctrl); background: var(--surface-2);
  font-size: 14px; font-family: inherit; color: var(--ink);
  outline: none;
  transition: border-color var(--motion-hover) var(--ease-standard), box-shadow var(--motion-hover) var(--ease-standard), background var(--motion-hover) var(--ease-standard);
}
.cp-input::placeholder { color: var(--ink-faint); }
.cp-input:focus { border-color: var(--accent-3); background: var(--surface); box-shadow: 0 0 0 3px rgba(45, 164, 162, 0.14); }
.cp-input:disabled { background: var(--surface-2); color: var(--ink-faint); }
.btn {
  height: 36px; padding: 0 20px;
  border: none; border-radius: var(--r-ctrl);
  background: var(--accent-grad); color: #fff; cursor: pointer; font-size: 14px;
  font-weight: 500; font-family: inherit; flex: none;
  box-shadow: 0 2px 6px rgba(45, 164, 162, 0.22);
  transition: filter var(--motion-hover) var(--ease-standard);
}
.btn:hover { filter: brightness(1.05); }
.btn:disabled { opacity: .55; cursor: not-allowed; box-shadow: none; }
</style>
