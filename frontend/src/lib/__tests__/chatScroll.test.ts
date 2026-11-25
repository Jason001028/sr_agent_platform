import { effectScope, nextTick, reactive, ref } from 'vue';
import { expect, it } from 'vitest';
import type { ChatMsg } from '../../stores/chat';
import { useChatScroll } from '../chatScroll';

it('preserves the reading position when a tool result arrives offscreen', async () => {
  const viewport = { scrollHeight: 1000, clientHeight: 300, scrollTop: 200 };
  const chat = reactive({ sessionId: 'session', messages: [] as ChatMsg[], streamTick: 0 });
  const scope = effectScope();
  const scroll = scope.run(() => useChatScroll(ref(viewport as HTMLElement), chat, 60))!;
  try {
    chat.messages.push({ seq: null, role: 'tool', content: '' });
    await nextTick();
    expect(viewport.scrollTop).toBe(200);
    expect(scroll.hasNewActivity.value).toBe(true);

    await scroll.scrollBottom();
    expect(viewport.scrollTop).toBe(1000);
    expect(scroll.hasNewActivity.value).toBe(false);

    viewport.scrollTop = 690;
    chat.messages.push({ seq: null, role: 'assistant', content: '完成' });
    await nextTick();
    viewport.scrollHeight = 1100;
    await nextTick();
    expect(viewport.scrollTop).toBe(1100);
  } finally {
    scope.stop();
  }
});
