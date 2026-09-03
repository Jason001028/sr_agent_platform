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
  height: calc(100vh - 72px);
  display: flex;
  gap: 12px;
  max-width: 1200px;
  margin: 0 auto;
}
.cp-side {
  width: 220px;
  flex: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 10px;
  overflow: auto;
}
.cp-sessions { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.cp-sessions li.cur .cp-sess-btn { background: #ecf5ff; color: #409eff; border-color: #a0cfff; }
.cp-sess-btn {
  width: 100%; text-align: left; padding: 6px 8px; font-size: 13px;
  border: 1px solid transparent; border-radius: 4px; background: transparent;
  color: #2c3e50; cursor: pointer; font-family: inherit;
}
.cp-sess-btn:hover:not(:disabled) { background: #f5f7fa; }
.cp-sess-btn:disabled { opacity: .6; cursor: not-allowed; }
.cp-side-hint { color: #909399; font-size: 12px; }

.cp-main {
  flex: 1; min-width: 0;
  display: flex; flex-direction: column;
  background: #fff; border: 1px solid #ebeef5; border-radius: 6px;
  overflow: hidden;
}
.cp-head {
  display: flex; align-items: baseline; gap: 10px;
  padding: 8px 14px; border-bottom: 1px solid #ebeef5; flex: none;
}
.cp-title { font-size: 15px; font-weight: 600; }
.cp-sessid { font-size: 12px; color: #909399; font-family: ui-monospace, monospace; }
.cp-sessid.empty { color: #c0c4cc; }
.cp-err { margin: 0; padding: 6px 14px; font-size: 13px; color: #f56c6c; background: #fef0f0; border-bottom: 1px solid #fde2e2; }

.cp-scroll { flex: 1; overflow: auto; padding: 14px; display: flex; flex-direction: column; gap: 8px; }
.cp-loading, .cp-empty, .cp-thinking { color: #909399; font-size: 13px; text-align: center; }
.cp-empty p { max-width: 520px; margin: 30px auto 0; line-height: 1.6; }

.cp-row { display: flex; flex-direction: column; }
.cp-row.user { align-items: flex-end; }
.bubble {
  max-width: 78%; padding: 8px 12px; border-radius: 8px;
  font-size: 13.5px; line-height: 1.55; white-space: pre-wrap; word-break: break-word;
}
.bubble.user { background: #409eff; color: #fff; border-top-right-radius: 2px; }
.bubble.assistant { background: #f4f6f8; border-top-left-radius: 2px; }
.bubble.assistant.err { background: #fef0f0; color: #d03050; }
.bubble.tools { background: #fafbfc; border: 1px dashed #dcdfe6; }
.tool-title { margin: 0 0 4px; font-size: 12px; color: #909399; }
.tool-decl { display: flex; gap: 8px; align-items: baseline; font-size: 13px; }
.tool-decl code { color: #409eff; font-family: ui-monospace, monospace; }
.tool-args { color: #909399; font-size: 12px; word-break: break-all; }

.tool-trace {
  align-self: flex-start; display: inline-flex; align-items: center; gap: 6px;
  font-size: 12px; color: #67c23a; background: #f0f9eb;
  border: 1px solid #e1f3d8; border-radius: 12px; padding: 2px 10px;
}
.tool-trace.fail { color: #f56c6c; background: #fef0f0; border-color: #fde2e2; }
.tool-ic.fail { color: #f56c6c; }
.tool-name { font-family: ui-monospace, monospace; font-weight: 600; }
.tool-detail { opacity: .85; }

.cp-inputbar { display: flex; gap: 8px; padding: 10px; border-top: 1px solid #ebeef5; flex: none; }
.cp-input {
  flex: 1; padding: 7px 10px; border: 1px solid #c0c4cc; border-radius: 4px;
  font-size: 14px; font-family: inherit;
}
.cp-input:disabled { background: #f5f7fa; color: #909399; }
.btn { padding: 7px 16px; border: 1px solid #409eff; border-radius: 4px; background: #409eff; color: #fff; cursor: pointer; font-size: 14px; flex: none; }
.btn:disabled { opacity: .55; cursor: not-allowed; }
</style>
