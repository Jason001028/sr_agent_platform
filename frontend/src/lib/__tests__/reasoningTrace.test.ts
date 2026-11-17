import { describe, expect, it } from 'vitest';
import type { ChatMsg } from '../../stores/chat';
import { reasoningViews, SHORT_THINKING_SEC, type ReasoningTiming } from '../reasoningTrace';

const msg = (over: Partial<ChatMsg>): ChatMsg => ({
  seq: null, role: 'assistant', content: '', ...over,
});
const keys = (messages: ChatMsg[]): string[] => messages.map((_, i) => `x${i}`);
const timing = (seconds: number, segments = 1): ReasoningTiming => ({ seconds, segments });

describe('reasoningViews', () => {
  it('merges consecutive short reasoning stages into the last block', () => {
    const messages: ChatMsg[] = [
      msg({ role: 'user', content: 'find it' }),
      msg({ reasoning: 'first thought', streaming: false, phase: null }),
      msg({ role: 'tool', toolName: 'search_scenes', ok: false, content: '' }),
      msg({ reasoning: 'second thought', streaming: false, phase: null }),
      msg({ content: 'answer', streaming: false, phase: null }),
    ];
    const timings = { x1: timing(2.4), x3: timing(3.1) };
    const views = reasoningViews(messages, keys(messages), timings);

    expect(views.has(1)).toBe(false);            // hidden into the merged block
    expect(views.get(3)).toMatchObject({
      text: 'first thought\n\nsecond thought',
      count: 2,
      merged: true,
    });
    expect(views.get(3)?.seconds).toBeCloseTo(5.5);
    // The failed tool row is not a reasoning view; caller keeps rendering it.
    expect(views.has(2)).toBe(false);
  });

  it('does not merge a long stage', () => {
    const messages: ChatMsg[] = [
      msg({ reasoning: 'short', phase: null }),
      msg({ reasoning: 'long', phase: null }),
    ];
    const views = reasoningViews(messages, keys(messages), {
      x0: timing(3),
      x1: timing(SHORT_THINKING_SEC + 1),
    });
    expect(views.get(0)).toMatchObject({ merged: false, seconds: 3 });
    expect(views.get(1)).toMatchObject({ merged: false, seconds: SHORT_THINKING_SEC + 1 });
    expect(views.get(1)?.text).toBe('long');
  });

  it('does not merge when a stage has no reliable timing', () => {
    const messages: ChatMsg[] = [
      msg({ reasoning: 'one', phase: null }),
      msg({ reasoning: 'two', phase: null }),
    ];
    const views = reasoningViews(messages, keys(messages), { x0: timing(2) });
    expect(views.get(0)?.seconds).toBe(2);
    expect(views.get(1)).toMatchObject({ merged: false, seconds: null });
    expect(views.get(1)?.text).toBe('two');
  });

  it('resets the run at a user message and keeps single stages individual', () => {
    const messages: ChatMsg[] = [
      msg({ role: 'user', content: 'one' }),
      msg({ reasoning: 'a', phase: null }),
      msg({ role: 'user', content: 'two' }),
      msg({ reasoning: 'b', phase: null }),
    ];
    const views = reasoningViews(messages, keys(messages), {
      x1: timing(2), x3: timing(2),
    });
    expect(views.get(1)).toMatchObject({ count: 1, merged: false });
    expect(views.get(3)).toMatchObject({ count: 1, merged: false });
  });

  it('keeps earlier short stages merged while a new stage streams', () => {
    const messages: ChatMsg[] = [
      msg({ reasoning: 'one', streaming: false, phase: null }),
      msg({ reasoning: 'two', streaming: false, phase: null }),
      msg({ reasoning: 'three', streaming: true, phase: 'thinking' }),
    ];
    const views = reasoningViews(messages, keys(messages), {
      x0: timing(2), x1: timing(1),
    });
    expect(views.get(1)).toMatchObject({
      text: 'one\n\ntwo', count: 2, merged: true,
    });
    expect(views.has(0)).toBe(false);
    expect(views.get(2)).toMatchObject({
      text: 'three', merged: false, seconds: null, streaming: true,
    });
  });

  it('counts multiple segments inside one message', () => {
    const messages: ChatMsg[] = [msg({ reasoning: 'again', phase: null })];
    const views = reasoningViews(messages, keys(messages), { x0: timing(4, 2) });
    expect(views.get(0)).toMatchObject({ count: 2, seconds: 4, merged: false });
  });
});
