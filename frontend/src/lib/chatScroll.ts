import { nextTick, ref, watch, type Ref } from 'vue';
import type { ChatMsg } from '../stores/chat';

export function useChatScroll(el: Ref<HTMLElement | null>, chat: {
  sessionId: string | null;
  messages: ChatMsg[];
  streamTick: number;
}, threshold: number) {
  const hasNewActivity = ref(false);

  function nearBottom() {
    const viewport = el.value;
    return !viewport || viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < threshold;
  }

  async function scrollBottom() {
    hasNewActivity.value = false;
    await nextTick();
    if (el.value) el.value.scrollTop = el.value.scrollHeight;
  }

  function followActivity() {
    if (nearBottom()) void scrollBottom();
    else hasNewActivity.value = true;
  }

  function onScroll() {
    if (nearBottom()) hasNewActivity.value = false;
  }

  // Measure the old scroll position before Vue appends the new content.
  watch([() => chat.sessionId, () => chat.messages.at(-1), () => chat.streamTick],
    ([session, last], [previousSession, previousLast]) => {
      if (session !== previousSession || last?.role === 'user' && last !== previousLast) void scrollBottom();
      else followActivity();
    });

  return { hasNewActivity, followActivity, onScroll, scrollBottom };
}
