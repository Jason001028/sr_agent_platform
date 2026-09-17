<script setup lang="ts">
/**
 * ChatPage.vue — Agent 聊天（阶段5，api-contract.md §3.2）
 * 布局：左侧会话列表（新建/切换），右侧消息 timeline + 发送框。
 * 消息 = SSE 逐帧归并（reduceChatSse）或刷新后 GET messages 投影，同一形状：
 *   工具回合渲染为 用户 ─ Assistant(声明工具) ─ 工具结果 ─ 最终回复。
 * mock LLM（SR_LLM_MOCK=1）每回合固定 search_scenes → 总结，可离线走通。
 */
import { nextTick, onMounted, ref, watch } from 'vue';
import { useChatStore } from '../stores/chat.js';
import type { ChatMsg } from '../stores/chat.js';

const chat = useChatStore();
const draft = ref('');

const scrollEl = ref<HTMLElement | null>(null);

function shortId(sid: string): string {
  return sid.length > 10 ? sid.slice(0, 8) + '…' : sid;
}

function argsText(a: Record<string, unknown>): string {
  const s = JSON.stringify(a);
  return s && s !== '{}' ? s : '（无参数）';
}

function toolIcon(ok?: boolean): string {
  if (ok === undefined) return '…';
  return ok ? '✓' : '✗';
}

function msgKey(m: ChatMsg, i: number): string {
  return m.seq !== null ? 's' + m.seq : 'x' + i;
}

function onSend(): void {
  const text = draft.value.trim();
  if (!text || chat.sending) return;
  draft.value = '';
  void chat.send(text);
}

async function scrollBottom(): Promise<void> {
  await nextTick();
  if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight;
}

watch(() => chat.messages.length, () => void scrollBottom());
onMounted(() => {
  void chat.init();
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
      <ul class="cp-sessions">
        <li v-for="s in chat.sessions" :key="s.session_id"
            :class="{ cur: s.session_id === chat.sessionId }">
          <button type="button" class="cp-sess-btn"
                  :disabled="chat.sending"
                  :title="'更新时间 ' + new Date(s.updated_at * 1000).toLocaleString()"
                  @click="chat.selectSession(s.session_id)">
            {{ shortId(s.session_id) }}
          </button>
        </li>
      </ul>
      <p v-if="!chat.loading && !chat.sessions.length" class="cp-side-hint">
        暂无会话，点「新建会话」开始。
      </p>
    </aside>

    <!-- 对话区 -->
    <section class="cp-main">
      <div class="cp-head">
        <span class="cp-title">Agent 聊天</span>
        <span v-if="chat.sessionId" class="cp-sessid">会话 {{ shortId(chat.sessionId) }}</span>
        <span v-else class="cp-sessid empty">未选择会话</span>
      </div>

      <p v-if="chat.error" class="cp-err">{{ chat.error }}</p>

      <div ref="scrollEl" class="cp-scroll">
        <p v-if="chat.loading" class="cp-loading">加载会话…</p>
        <div v-else-if="!chat.messages.length" class="cp-empty">
          <p>离线试跑提示：当前运行模式（SR_LLM_MOCK / SR_SLURM_FAKE / 盘阵）由服务端 env
            决定，聊天可发任意话术验证链路。</p>
        </div>

        <div v-for="(m, i) in chat.messages" :key="msgKey(m, i)" class="cp-row" :class="m.role">
          <!-- user -->
          <template v-if="m.role === 'user'">
            <div class="bubble user">{{ m.content }}</div>
          </template>

          <!-- assistant -->
          <template v-else-if="m.role === 'assistant'">
            <div v-if="m.isError" class="bubble assistant err">{{ m.content }}</div>
            <div v-else-if="m.content" class="bubble assistant">{{ m.content }}</div>
            <div v-else-if="m.toolCalls && m.toolCalls.length" class="bubble assistant tools">
              <p class="tool-title">Agent 声明调用工具</p>
              <div v-for="tc in m.toolCalls" :key="tc.name" class="tool-decl">
                <code>{{ tc.name }}</code>
                <span class="tool-args">{{ argsText(tc.args) }}</span>
              </div>
            </div>
            <div v-else-if="chat.sending" class="bubble assistant tools">
              <p class="tool-title">正在思考…</p>
            </div>
          </template>

          <!-- tool 结果行 -->
          <template v-else>
            <div class="tool-trace" :class="{ fail: m.ok === false }">
              <span class="tool-ic" :class="{ fail: m.ok === false }">{{ toolIcon(m.ok) }}</span>
              <span class="tool-name">{{ m.toolName ?? '工具' }}</span>
              <span v-if="m.detail" class="tool-detail">{{ m.detail }}</span>
            </div>
          </template>
        </div>

        <p v-if="chat.sending" class="cp-thinking">正在思考…</p>
      </div>

      <div class="cp-inputbar">
        <input v-model="draft" class="cp-input" type="text"
               placeholder="给 Agent 下指令（离线 mock 每回合固定检索盘阵场景）…"
               :disabled="chat.sending || !chat.hasSession()"
               @keyup.enter="onSend()" />
        <button type="button" class="btn" :disabled="chat.sending || !draft.trim()"
                @click="onSend()">
          {{ chat.sending ? '处理中…' : '发送' }}
        </button>
      </div>
    </section>
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
  gap: 10px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-panel);
  box-shadow: var(--shadow-card);
  padding: 12px;
  overflow: auto;
}
.cp-sessions { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.cp-sessions li.cur .cp-sess-btn {
  background: var(--accent-soft);
  color: var(--accent-deep);
  border-color: rgba(61, 169, 164, 0.35);
  font-weight: 600;
}
.cp-sess-btn {
  width: 100%; text-align: left; padding: 8px 10px; font-size: 13px;
  border: 1px solid transparent; border-radius: var(--r-ctrl);
  background: transparent;
  color: var(--ink-body); cursor: pointer; font-family: inherit;
  transition: background 0.15s ease;
}
.cp-sess-btn:hover:not(:disabled) { background: var(--surface-2); }
.cp-sess-btn:disabled { opacity: .6; cursor: not-allowed; }
.cp-side-hint { color: var(--ink-sub); font-size: 12px; line-height: 1.7; }
.cp-side .btn { width: 100%; }

.cp-main {
  flex: 1; min-width: 0;
  display: flex; flex-direction: column;
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-panel);
  box-shadow: var(--shadow-card);
  overflow: hidden;
}
.cp-head {
  display: flex; align-items: baseline; gap: 10px;
  padding: 12px 18px; border-bottom: 1px solid var(--line); flex: none;
  background: var(--surface);
}
.cp-title { font-size: 16px; font-weight: 600; color: var(--ink); }
.cp-sessid { font-size: 12px; color: var(--ink-sub); font-family: var(--font-mono); }
.cp-sessid.empty { color: var(--ink-faint); }
.cp-err {
  margin: 0; padding: 7px 18px; font-size: 13px; color: var(--err);
  background: var(--err-bg); border-bottom: 1px solid var(--err-line);
}

.cp-scroll { flex: 1; overflow: auto; padding: 18px; display: flex; flex-direction: column; gap: 10px; background: var(--surface); }
.cp-loading, .cp-empty, .cp-thinking { color: var(--ink-sub); font-size: 13px; text-align: center; }
.cp-empty p { max-width: 520px; margin: 30px auto 0; line-height: 1.7; }

.cp-row { display: flex; flex-direction: column; }
.cp-row.user { align-items: flex-end; }
.bubble {
  max-width: 78%; padding: 9px 14px; border-radius: 14px;
  font-size: 13.5px; line-height: 1.6; white-space: pre-wrap; word-break: break-word;
}
.bubble.user { background: var(--accent-grad); color: #fff; border-bottom-right-radius: 4px; box-shadow: 0 2px 6px rgba(45,164,162,0.18); }
.bubble.assistant { background: var(--surface-2); color: var(--ink-body); border-bottom-left-radius: 4px; }
.bubble.assistant.err { background: var(--err-bg); color: var(--err); }
.bubble.tools { background: #fbfcfa; border: 1px dashed var(--line); border-radius: 12px; }
.tool-title { margin: 0 0 4px; font-size: 12px; color: var(--ink-sub); }
.tool-decl { display: flex; gap: 8px; align-items: baseline; font-size: 13px; }
.tool-decl code { color: var(--accent-deep); font-family: var(--font-mono); }
.tool-args { color: var(--ink-sub); font-size: 12px; word-break: break-all; }

.tool-trace {
  align-self: flex-start; display: inline-flex; align-items: center; gap: 7px;
  font-size: 12px; color: var(--ok); background: var(--ok-bg);
  border: 1px solid var(--ok-line); border-radius: var(--r-pill); padding: 3px 12px;
}
.tool-trace.fail { color: var(--err); background: var(--err-bg); border-color: var(--err-line); }
.tool-ic.fail { color: var(--err); }
.tool-name { font-family: var(--font-mono); font-weight: 600; }
.tool-detail { opacity: .85; }

.cp-inputbar {
  display: flex; gap: 10px; padding: 12px 16px;
  border-top: 1px solid var(--line); flex: none;
  background: var(--surface);
}
.cp-input {
  flex: 1; height: 36px; padding: 0 12px; border: 1px solid var(--line);
  border-radius: var(--r-ctrl); background: var(--surface-2);
  font-size: 14px; font-family: inherit; color: var(--ink);
  outline: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease, background 0.15s ease;
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
  transition: filter 0.15s ease;
}
.btn:hover { filter: brightness(1.05); }
.btn:disabled { opacity: .55; cursor: not-allowed; box-shadow: none; }
</style>
