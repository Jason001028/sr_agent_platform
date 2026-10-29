/**
 * composables/useChatShot.ts — 「把左侧卡片截进对话」的共用动作
 * ------------------------------------------------------------------
 * 查看器侧的卡片相机键、聊天侧的截图选择器都走这里，保证两处的上限判断、
 * 连点保护、失败提示口径一致。
 *
 * 这一层不显示 UI：返回 `{ok, message}`，由调用方决定弹在哪儿（查看器有 toast，
 * /chat 页没有，用内联提示）。截图本身在 `viewer.captureForChat`，这里只管
 * 「能不能加、加得进加不进」。
 */
import { ref } from 'vue';
import { useChatStore } from '../stores/chat.js';
import { useViewerStore } from '../stores/viewer';
import { SHOT_MAX_COUNT } from '../lib/chatShot.js';

export interface ShotAddResult {
  ok: boolean;
  /** 失败原因（成功时为空串）。已写成可直接展示的一句话。 */
  message: string;
}

export function useChatShot() {
  const viewer = useViewerStore();
  const chat = useChatStore();
  /** 截图/取像素期间置位：一张 4K 图的缩略图重绘要几十毫秒，连点会叠出多张。 */
  const busy = ref(false);

  const full = (): ShotAddResult => ({
    ok: false, message: `每条消息最多 ${SHOT_MAX_COUNT} 张截图，先删一张再加`,
  });

  async function add(recId: number): Promise<ShotAddResult> {
    if (busy.value) return { ok: false, message: '正在准备截图…' };
    if (chat.pendingImages.length >= SHOT_MAX_COUNT) return full();
    const name = viewer.recs.find((r) => r.id === recId)?.name ?? '';
    busy.value = true;
    try {
      const shot = await viewer.captureForChat(recId);
      // captureForChat 已把具体原因写进 viewer 的 toast；这里给的是同一件事的短句
      if (!shot) return { ok: false, message: `「${name}」截图未完成，请查看画布中的具体原因后重试` };
      if (!chat.addPendingImage(shot)) return full();
      return { ok: true, message: '' };
    } finally {
      busy.value = false;
    }
  }

  return { busy, add };
}
