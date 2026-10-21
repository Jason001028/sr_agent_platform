/**
 * lib/streaming.ts — 聊天流式输出开关（浏览器本地偏好）
 * ------------------------------------------------------------------
 * 服务端 SR_LLM_STREAM 是总开关（缺省开）；这里只是每个浏览器的「本回合要不要
 * 逐 token 上屏」偏好：缺省开，关闭后后端退回一次性调用（老事件序列）。
 * 设置浮层与发送请求共用这一处，别在别处再读 localStorage。
 */
const KEY = 'sr.chat.stream';

export function isStreamingEnabled(): boolean {
  try {
    if (typeof localStorage === 'undefined') return true;  // 非浏览器环境按默认开
    return localStorage.getItem(KEY) !== '0';
  } catch {
    return true;                      // 隐私模式读不到 → 按默认开处理
  }
}

export function setStreamingEnabled(enabled: boolean): void {
  try {
    localStorage.setItem(KEY, enabled ? '1' : '0');
  } catch {
    /* 隐私模式写不进去：本次会话内仍按调用方传入的值走 */
  }
}
