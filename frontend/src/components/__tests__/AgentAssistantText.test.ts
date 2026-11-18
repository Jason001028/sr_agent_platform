// @vitest-environment jsdom
import { createApp, h, nextTick, ref } from 'vue';
import { expect, it } from 'vitest';
import type { ChatMsg } from '../../stores/chat';
import AgentAssistantText from '../AgentAssistantText.vue';

it('folds inspection tool commentary in developer mode and keeps the final answer visible', async () => {
  const message = ref<ChatMsg>({
    role: 'assistant', seq: 2, content: '本快照 current=null，先取第 1 页',
    toolCalls: [{ id: 'qc', name: 'inspect_repair_workspace', args: {} }],
  });
  const developerMode = ref(false);
  const root = document.createElement('div');
  const app = createApp(() => h(AgentAssistantText, { message: message.value, developerMode: developerMode.value }));
  app.mount(root);
  expect(root.textContent).toBe('');
  developerMode.value = true;
  await nextTick();
  expect(root.querySelector('details')?.open).toBe(false);
  expect(root.textContent).toContain('current=null');
  message.value = { role: 'assistant', seq: 4, content: '全部质检结束。' };
  await nextTick();
  expect(root.querySelector('.bubble.assistant')?.textContent).toContain('全部质检结束。');
  app.unmount();
});
