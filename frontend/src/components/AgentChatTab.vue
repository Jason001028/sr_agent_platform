<script setup lang="ts">
import { imageRef } from '../lib/spatial';
import ThinkingSlider from "./ThinkingSlider.vue";
import AgentRepairSettings from './AgentRepairSettings.vue';
import HoldConfirmButton from './HoldConfirmButton.vue';
import pauseIcon from '../../icon/pause.svg?url';
import playIcon from '../../icon/play.svg?url';
import squareIcon from '../../icon/square.svg?url';
import type AgentRepairPlanCard from './AgentRepairPlanCard.vue';
import { useAgentPlanRevisions } from '../lib/agentPlanRevisions';
/**
 * AgentChatTab.vue — 上下文侧舱 [Agent] tab（阶段6 L2）
 * ------------------------------------------------------------------
 * - 支持已加载的本地、上传及服务器影像；未就绪时提示原因，批次移交也可启用对话。
 * - 与 /chat **同一** chat store / 会话 / 后端。这里**不 init**（进查看器不自动挑会话，
 *   首问才 apiCreateSession），但头部给了会话三件事（三个 16px 图标键，2026-10-23）：
 *   新建（circle-plus）/ 历史（clock，点开切换）/ 删除当前（circle-x）。历史列表
 *   **打开菜单时才拉**（`refreshSessions`），不用会话管理就一个请求都不多发。
 *   原先头部左侧那串「会话 xxxxxxxx」已去掉 —— 它对操作没有帮助，当前会话在历史列表里
 *   高亮即已说明。
 * - 每个发送 = agentPayload(question, buildContextNote(snap)) —— question 原样 + CTX_DIVIDER
 *   + 自动上下文快照（图像名 / 原图 W×H / 显示尺寸 / 拉伸 + 选中 ROI 的确定性统计）。
 *   Agent 只解读/建议，数字一律来自 buildStats，不得编造（见 agentContext.ts 纪律）。
 * - 渲染形状与 /chat 一致（SSE 归并出的 ChatMsg）；工具回合折成单行摘要。用户气泡只显示
 *   原话：CTX_DIVIDER 之后的自动附文默认不进渲染，只有开发者模式在气泡下给一块浅米色
 *   折叠面板（`<details>`，默认收起）—— 排查「Agent 到底看到了什么」时才展开。
 */
import { shallowReactive, computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import { useViewerStore } from '../stores/viewer';
import { useChatStore } from '../stores/chat.js';
import type { ChatMsg } from '../stores/chat.js';
import AnnotationManager from './AnnotationManager.vue';
import CurrentImagePill from './CurrentImagePill.vue';
import AgentAssistantText from './AgentAssistantText.vue';
import UserMessageText from './UserMessageText.vue';
import { ANALYZE_CURRENT_PROMPT } from '../lib/analysisPrompt';
import ReasoningBlock from './ReasoningBlock.vue';
import { phaseLabel } from '../lib/executionOverview.js';
import ThinkingIndicator from './ThinkingIndicator.vue';
import ChatAttachTray from './ChatAttachTray.vue';
import GoalClarifications from './GoalClarifications.vue';
import ChatImagePicker from './ChatImagePicker.vue';
import ChatBubbleImages from './ChatBubbleImages.vue';
import AppIcon from './AppIcon.vue';
import AgentBotIcon from './AgentBotIcon.vue';
import { useScenesStore } from '../stores/scenes.js';
import {
  agentPayload, buildContextNote, splitAgentPayload,
} from '../lib/agentContext.js';
import type { ViewerContextSnap } from '../lib/agentContext.js';
import { isDeveloperMode } from '../lib/developerMode.js';
import { reasoningViews, type ReasoningTiming } from '../lib/reasoningTrace';
import { roiOrigGeom } from '../lib/roiStats.js';

import AgentToolPresentation from './AgentToolPresentation.vue';
import { useChatScroll } from '../lib/chatScroll';
import { projectAgentActivity } from '../lib/agentActivity';
import type { BatchAgentHandoff } from '../lib/batchHandoff';

const viewer = useViewerStore();
const chat = useChatStore();
const toolPresentation = computed(() => projectAgentActivity(chat.messages, chat.sending && !chat.recovery));
const { resolvePlan, revisePlan } = useAgentPlanRevisions(() => chat.sessionId);
const inlinePlanCards = shallowReactive(new Map<string, {
  index: number; card: InstanceType<typeof AgentRepairPlanCard>;
}>());
function registerInlinePlanCard(key: string, index: number, card: InstanceType<typeof AgentRepairPlanCard> | null) {
  if (!card) {
    inlinePlanCards.delete(key);
    return;
  }
  const previous = inlinePlanCards.get(key);
  if (previous?.card !== card || previous.index !== index) inlinePlanCards.set(key, { index, card });
}
const controlledTaskCard = computed(() => {
  if (draft.value.trim() || chat.pendingImages.length) return null;
  return [...inlinePlanCards.values()].sort((a, b) => b.index - a.index)
    .find(entry => entry.card.taskControl)?.card ?? null;
});
const taskControl = computed(() => controlledTaskCard.value?.taskControl ?? null);
const taskControlIcons = { pause: pauseIcon, play: playIcon, square: squareIcon };

function onPrimaryAction() {
  if (taskControl.value) {
    if (!taskControl.value.busy) void controlledTaskCard.value?.togglePause();
    return;
  }
  void onSend();
}

const scenes = useScenesStore();
const draft = ref('');
/** 交接后的输入区解锁：清单场景通常没有已加载的活动影像，只靠 `enabled` 会让底部
 *  一直罩着「未打开影像」的锁定遮罩。解锁跟着交接走，直到显式新建/切换/删除会话
 *  —— 已发出的消息本身就是上下文，追问不该因为没图又被锁回去。 */
const batchContext = ref(false);
/** 「已携带批次上下文」提示行只在交接尚未发出时显示；发送后提示收掉，解锁保留。 */
const batchHint = ref(false);
const composerEnabled = computed(() => enabled.value || batchContext.value);
function clearBatchContext(): void {
  batchContext.value = false;
  batchHint.value = false;
}
function receiveBatch(event: Event) {
  const detail = (event as CustomEvent<BatchAgentHandoff>).detail;
  const text = detail?.text;
  if (!text) return;
  batchContext.value = true;
  if (detail.autoSend && detail.repairSettings) {
    void sendAgentRepair(detail);
    return;
  }
  batchHint.value = true;
  draft.value = draft.value.trim() ? `${draft.value}\n\n${text}` : text;
}
onMounted(() => window.addEventListener('batch-agent-handoff', receiveBatch));
onBeforeUnmount(() => window.removeEventListener('batch-agent-handoff', receiveBatch));
const scrollEl = ref<HTMLElement | null>(null);
/** 截图选择器（截查看器当前画面，来源是左侧影像列表）。 */
const pickerOpen = ref(false);
async function openSearchScene(row: Record<string, unknown>): Promise<void> {
  const name = String(row.name ?? row.sceneId ?? row.id ?? '').trim();
  if (name) await scenes.openByName(name);
}

const rec = computed(() => viewer.activeRec);
/** 当前影像已加载且尺寸有效即可使用，不要求场景库身份。 */
const enabled = computed(() => Boolean(rec.value?.thumb && rec.value.W > 0 && rec.value.H > 0));
/** 无会话时首问会自动建会话 —— 输入框始终可输入；placeholder 提示差异。 */
const placeholder = computed(() =>
  chat.sessionId
    ? '询问当前区域…'
    : '输入问题即自动新建会话并附上下文…');
const reason = computed(() => {
  const r = rec.value;
  if (!r) return '当前未打开影像。请打开本地文件、上传影像或服务器影像，也可从修复清单携带批次上下文。';
  return '当前影像尚未加载完成，准备就绪后即可使用 Agent。';
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
watch(enabled, (ok) => { if (!ok) pickerOpen.value = false; });

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
  clearBatchContext();
  await chat.newSession();
}

async function switchTo(sid: string): Promise<void> {
  menuOpen.value = false;
  if (chat.sending || sid === chat.sessionId) return;
  clearBatchContext();
  await chat.selectSession(sid);
}

/** 删除当前会话及其消息、用量和轨迹。 */
async function removeCurrent(): Promise<void> {
  const sid = chat.sessionId;
  if (!sid || chat.sending) return;
  menuOpen.value = false;
  clearBatchContext();
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

/* ---- 思考阶段计时 / 连续短阶段合并（ReasoningBlock 上报，不改 Agent Loop） ---- */
const reasoningTimings = reactive<Record<string, ReasoningTiming>>({});
const reasoning = computed(() => reasoningViews(
  chat.messages, chat.messages.map((m, i) => msgKey(m, i)), reasoningTimings));
function reasoningView(index: number) {
  return reasoning.value.get(index);
}
function onReasoningTiming(index: number, value: ReasoningTiming): void {
  const message = chat.messages[index];
  if (message) reasoningTimings[msgKey(message, index)] = value;
}
watch(() => chat.sessionId, () => {
  for (const key of Object.keys(reasoningTimings)) delete reasoningTimings[key];
});
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
/** 队尾仍在流式接收的 assistant 消息（思考/回答/工具声明阶段指示用）。 */
const liveMsg = computed<ChatMsg | null>(() => {
  const last = chat.messages[chat.messages.length - 1];
  return last && last.role === 'assistant' && last.streaming ? last : null;
});

function waitLabel(m: ChatMsg): string {
  return chat.activity.label || phaseLabel(m.phase);
}

const creating = ref(false);   // 防止双击并发 newSession 建出两个会话

async function sendAgentRepair(handoff: BatchAgentHandoff): Promise<void> {
  if (!handoff.repairSettings) return;
  if (chat.sending || creating.value) {
    chat.error = 'Agent 正在处理上一条请求，请结束后重新点击 Agent协助。';
    return;
  }
  if (!chat.sessionId) {
    creating.value = true;
    try { await chat.newSession(); }
    finally { creating.value = false; }
  }
  if (!chat.sessionId) return;
  if (handoff.isCurrent && !handoff.isCurrent()) {
    chat.error = '清单已更换或关闭，请重新点击 Agent协助。';
    return;
  }
  await chat.send(handoff.text, { repairSettings: handoff.repairSettings });
}

/** 有截图时可以不写字：上下文快照照样附上（Agent 仍能引用 W×H / 拉伸）。 */
function canSend(): boolean {
  return composerEnabled.value && (snap.value !== null || batchContext.value) && !chat.sending && !creating.value
    && (Boolean(draft.value.trim()) || chat.pendingImages.length > 0);
}

async function onSend(): Promise<void> {
  let text = draft.value.trim();
  const s = snap.value;
  if (!canSend() || (!s && !batchContext.value)) return;
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
  // Opening a session may await the network; recheck access after image changes.
  if (!canSend()) return;
  const region=viewer.questionRegion;
  if(region) {
    const source=viewer.recs.find(r=>r.id===region.recId);
    if(!source || imageRef(source)?.image_key!==region.key || source.id!==viewer.activeRec?.id) { chat.error='区域来源影像已变化，请返回原影像后重试。'; return; }
    const shot=await viewer.captureForChat(region.recId,region.bounds);
    if(!shot || !chat.addPendingImage(shot)) { chat.error='无法添加区域截图，请重试。'; return; }
    text += '\nUser source ROI (not AI detection): '+JSON.stringify(region)+'; attachment is a display preview crop, not raw DN.';
    viewer.questionRegion=null;
  }
  draft.value = '';
  const latest = snap.value;
  // 提示行收起，但输入区保持解锁：本会话已经有交接上下文，追问不再依赖当前影像。
  batchHint.value = false;
  void chat.send(latest ? agentPayload(text, buildContextNote(latest)) : text);
}

async function analyzeCurrent() {
  if (!rec.value || chat.sending) return;
  const recId=rec.value.id;
  const shot=await viewer.captureForChat(recId);
  if (!shot || rec.value?.id!==recId || !chat.addPendingImage(shot)) return;
  if (!draft.value.trim()) draft.value = ANALYZE_CURRENT_PROMPT;
  await onSend();
}
function annotationConversation(event: Event) {
  const id=(event as CustomEvent<string>).detail;
  void nextTick(()=>scrollEl.value?.querySelector<HTMLElement>('[data-annotation-id="'+CSS.escape(id)+'"]')?.scrollIntoView({block:'nearest'}));
}
onMounted(()=>window.addEventListener('annotation-conversation',annotationConversation));
onBeforeUnmount(()=>window.removeEventListener('annotation-conversation',annotationConversation));
const { hasNewActivity, followActivity, onScroll, scrollBottom } = useChatScroll(scrollEl, chat, 60);
</script>

<template>
  <div class="at">
    <!-- 头部：左侧一组会话操作（新建 / 历史 / 删除 / 设置）+ 去 Chat 页。
         不再显示「会话 xxxxxxxx」：当前会话在历史菜单里高亮，那串 id 对操作没有帮助。 -->
    <div class="at-head">
      <!-- 锁定只影响输入；历史查看与会话管理始终可用。 -->
      <div class="at-sess-tools">
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
        <HoldConfirmButton :key="chat.sessionId ?? undefined" class="at-hbtn danger" data-e2e="agent-del"
          :disabled="!chat.sessionId || chat.sending" label="删除当前会话" @confirm="removeCurrent">
          <AppIcon name="circle-x" :size="16" />
        </HoldConfirmButton>
        <AgentRepairSettings />
      </div>
    </div>

    <!-- 会话菜单（下拉）+ 点外部关闭的透明捕手 -->
    <template v-if="menuOpen">
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

    <CurrentImagePill v-if="enabled && rec" :name="rec.name" :busy="chat.sending || creating" @analyze="analyzeCurrent" />
    <AnnotationManager />
    <p v-if="chat.error" class="at-err" role="alert">{{ chat.error }}</p>
    <div v-if="chat.recovery" class="at-err">
      <span role="status">{{ chat.recovery.message }}</span>
      <button v-if="!chat.recovery.resolved && !chat.recovery.checking" type="button"
              class="btn" @click="chat.reconcileTurn()">查询本轮状态</button>
    </div>

    <!-- 会话区 -->
      <div ref="scrollEl" class="at-scroll" data-agent-transcript @scroll.passive="onScroll">
        <p v-if="creating && !chat.messages.length" class="at-desc" role="status">正在创建 Agent 会话…</p>
        <div v-else-if="!chat.messages.length && !chat.sending" class="at-welcome">
          <AgentBotIcon :status="chat.agentStatus" :size="24" label="遥感影像助手" />
          <div class="bubble assistant">你好，我是遥感影像助手。你可以让我检索场景、打开影像或规划处理任务。</div>
        </div>

        <div v-for="(m, i) in chat.messages" :key="msgKey(m, i)" class="at-row" :class="m.role">
          <!-- user：截图在上；原话为空（纯图消息）时不渲染空段 -->

          <template v-if="m.role === 'user'">
            <div class="bubble user" :class="{ 'has-shots': Boolean(m.images?.length) }">
              <ChatBubbleImages v-if="m.images?.length" :images="m.images" />
              <!-- 原话。自动附的查看器上下文（CTX_DIVIDER 之后）默认不上屏，只在开发者
                   模式下方折叠块里给；历史 /chat 消息没有分隔线，整条都是原话。 -->
              <UserMessageText v-if="userText(m.content)" class="uq" :text="userText(m.content)" />
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
              <ReasoningBlock v-if="m.reasoning && reasoningView(i)"
                              :text="reasoningView(i)?.text ?? m.reasoning"
                              :streaming="reasoningView(i)?.streaming ?? false"
                              :phase="reasoningView(i)?.phase ?? null"
                              :activity="m.streaming ? chat.activity.label : undefined"
                              :seconds="reasoningView(i)?.seconds ?? null"
                              :segment-count="reasoningView(i)?.count ?? 1"
                              :merged-count="reasoningView(i)?.merged ? reasoningView(i)?.count : undefined"
                              :merged-seconds="reasoningView(i)?.merged ? reasoningView(i)?.seconds : undefined"
                              @timing="onReasoningTiming(i, $event)" />
              <AgentAssistantText v-if="m.content" :message="m" :developer-mode="developerMode" />
              <div v-else-if="chat.sending && !m.toolCalls?.length && ((m.streaming && !m.reasoning) || m.phase === 'tool_call')"
                   class="bubble assistant tools">
                <ThinkingIndicator :label="waitLabel(m)" show-elapsed />
              </div>
            </template>
          </template>

          <AgentToolPresentation :projection="toolPresentation" :index="i" :session-key="chat.sessionId"
            :developer-mode="developerMode" :resolve-plan="resolvePlan" :plan-card-ref="registerInlinePlanCard"
            @open-scene="openSearchScene" @plan-revised="revisePlan" @tool-calls-updated="followActivity" />
        </div>

        <GoalClarifications :session-id="chat.sessionId" @updated="!chat.sending && chat.sessionId && chat.loadHistory(chat.sessionId)" />
        <div v-if="chat.sending && !liveMsg && !chat.recovery && !chat.activity.pending?.length" class="at-thinking">
          <ThinkingIndicator :label="chat.activity.label" show-elapsed />
        </div>
      </div>

      <button v-if="hasNewActivity" type="button" class="chat-new-activity" @click="scrollBottom">有新活动 ↓</button>
      <div class="at-foot" :class="{ locked: !taskControl && !composerEnabled }">
        <div :inert="!composerEnabled">
        <p v-if="batchHint" class="at-desc">已携带批次上下文，可补充问题后发送。</p>

        <div v-if="viewer.questionRegion" class="roi-reference"><button @click="viewer.activeRec?.id===viewer.questionRegion.recId && viewer.focusSourceBounds(viewer.activeSide,viewer.questionRegion.bounds)">{{ viewer.questionRegion.name }} · 源影像区域 {{ viewer.questionRegion.bounds.join(', ') }}</button><button @click="viewer.questionRegion=null">移除</button><small>手动发送消息时附上区域预览截图。</small></div>
        <ChatAttachTray :items="chat.pendingImages" @remove="chat.removePendingImage" />
        </div>
        <div class="at-inputbar" :inert="!taskControl && !composerEnabled">
          <ThinkingSlider v-model="chat.thinkingProfile" :status="chat.thinkingStatus" :disabled="!enabled" />
          <button type="button" class="at-shot" data-e2e="chat-shot-open"
                  aria-label="添加截图" title="添加截图（截取当前视野）"
                  :disabled="!enabled || chat.sending || creating"
                  @click="pickerOpen = true">
            <AppIcon name="camera" :size="14" />
          </button>
          <input v-model="draft" class="at-input" type="text"
                 :placeholder="placeholder"
                 :disabled="!composerEnabled || chat.sending || creating"
                 @keyup.enter="onSend()" />
          <button type="button" class="cbtn" :class="{ 'task-primary': !!taskControl }"
            :disabled="taskControl ? taskControl.busy : !canSend()"
            :aria-label="taskControl?.label ?? '发送'" :title="taskControl?.label"
            :aria-busy="taskControl?.busy" @click="onPrimaryAction">
            <span v-if="taskControl" class="task-primary-icon" aria-hidden="true"
              :style="{ maskImage: `url(${taskControlIcons[taskControl.icon]})` }" />
            <template v-else>{{ chat.sending || creating ? '…' : '发送' }}</template>
          </button>
        </div>
        <div v-if="!taskControl && !composerEnabled" class="at-input-lock" role="status" aria-live="polite">
          <p class="at-desc">Agent 支持本地、上传及服务器影像</p>
          <p class="at-reason">{{ reason }}</p>
        </div>
      </div>
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
/* 设置键并进左侧三键一组：外壳与 .at-hbtn 同为 24×24，四颗图标中心与右边界齐平
   （AgentRepairSettings 默认 28×28，只在这个头部里收一档）。 */
.at-sess-tools :deep(.settings-trigger) {
  width: 24px;
  height: 24px;
  box-sizing: border-box;
  border: 1px solid transparent;
  border-radius: var(--r-ctrl);
  transition: border-color var(--motion-hover) var(--ease-standard), background var(--motion-hover) var(--ease-standard);
}
.at-sess-tools :deep(.settings-trigger:hover),
.at-sess-tools :deep(.settings-trigger[aria-expanded="true"]) {
  border-color: var(--accent-2);
  background: var(--accent-soft);
}
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

.at-input-lock { position: absolute; inset: 0; display: flex; flex-direction: column; justify-content: center; gap: 7px; padding: 16px; background: rgba(244, 248, 246, .25); backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px); border: 1px solid rgba(185, 204, 196, .28); border-radius: 10px 10px 0 0; }
.at-foot { position: relative; }
.at-foot.locked { min-height: 132px; display: grid; align-items: end; }
.at-welcome { display: flex; align-items: flex-start; gap: 10px; padding: 10px 2px; }
.at-welcome .bubble { flex: 1; min-width: 0; margin: 0; line-height: 1.8; font-size: 13px; }
.at-desc { margin: 0; font-size: 12.5px; color: var(--ink); font-weight: 600; }
.at-reason {
  margin: 0; font-size: 12px; color: var(--ink-sub); line-height: 1.7;
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
  box-shadow: 0 1px 2px rgba(0,0,0,.04), 0 4px 12px rgba(0,0,0,.06), 0 12px 32px rgba(0,0,0,.08);
}
.bubble.assistant.err { white-space: pre-wrap; background: var(--err-bg); color: var(--err); border-color: var(--err-line); }
.bubble.assistant.tools { width:100%; max-width:100%; padding:0; background:transparent; border:0; box-shadow:none; }
.at-row :deep(.reason) { max-width: 96%; }
/* 有正文时工具声明缩成正文脚注，不再单占一个虚线气泡 */
/* 托板与输入行同属底部一块：分隔线画在块顶，缩略图不悬在消息区里 */
.at-foot { flex: none; border-top: 1px solid var(--line); background: var(--surface); }
.at-inputbar {
  flex: none;
  display: flex; align-items: center; gap: 6px;
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
.task-primary { margin-inline-start:auto; display:grid; place-items:center; min-inline-size:48px; }
.task-primary-icon { width:18px; height:18px; background:currentColor; mask-repeat:no-repeat; mask-position:center; mask-size:contain; }
.cbtn:hover { filter: brightness(1.05); }
.cbtn:disabled { opacity: .55; cursor: not-allowed; }
.at-row:empty { display:none; }
.bubble.tools { background:transparent; border:0; box-shadow:none; padding:0; }
</style>
