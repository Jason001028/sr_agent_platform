/**
 * Agent 聊天状态（阶段5 实现）
 * ------------------------------------------------------------------
 * 对接 backend/agent/loop.py 的会话：SSE 流式推送 Agent 过程，REST 发起动作。
 * 阶段5 先写 REST/SSE 契约文档再写代码。
 */
import { defineStore } from 'pinia';
import { ref } from 'vue';

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'tool';
  content: string;
}

export const useChatStore = defineStore('chat', () => {
  const messages = ref<ChatMessage[]>([]);
  const sending = ref(false);

  return { messages, sending };
});
