// @vitest-environment jsdom
import { createApp, nextTick } from 'vue';
import { expect, it, vi } from 'vitest';
import HoldConfirmButton from '../HoldConfirmButton.vue';

it('cancels an early release and confirms once after a full hold', async () => {
  vi.useFakeTimers();
  const confirm = vi.fn();
  const root = document.createElement('div');
  const app = createApp(HoldConfirmButton, { label: '删除记录', onConfirm: confirm });
  app.mount(root);
  const button = root.querySelector('button')!;
  function pointer(type: string) {
    button.dispatchEvent(Object.assign(new MouseEvent(type, { button: 0 }), { pointerId: 1 }));
  }
  try {
    button.click();
    pointer('pointerdown');
    await nextTick();
    expect(button.classList.contains('is-holding')).toBe(true);
    vi.advanceTimersByTime(999);
    expect(confirm).not.toHaveBeenCalled();
    pointer('pointerup');
    await nextTick();
    expect(button.classList.contains('is-holding')).toBe(false);
    vi.advanceTimersByTime(1);
    expect(confirm).not.toHaveBeenCalled();
    pointer('pointerdown');
    vi.advanceTimersByTime(1000);
    expect(confirm).toHaveBeenCalledOnce();
    vi.advanceTimersByTime(1000);
    pointer('pointerup');
    expect(confirm).toHaveBeenCalledOnce();

    button.dispatchEvent(new KeyboardEvent('keydown', { key: ' ' }));
    vi.advanceTimersByTime(500);
    button.dispatchEvent(new KeyboardEvent('keyup', { key: ' ' }));
    vi.advanceTimersByTime(500);
    expect(confirm).toHaveBeenCalledOnce();
    button.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    vi.advanceTimersByTime(1000);
    expect(confirm).toHaveBeenCalledTimes(2);
  } finally {
    app.unmount();
    vi.useRealTimers();
  }
});
