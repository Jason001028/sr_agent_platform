<script setup lang="ts">
/**
 * JobNoticeStack.vue — 右下角任务提醒栈（2026-09-22）
 * ------------------------------------------------------------------
 * 「超分跑完了 / 跑失败了」的落点，**挂在 App 外壳上**（不是查看器页）：提醒的价值
 * 就在于人在别的页面干活时也能收到 —— 提交完切去查看器看影像的人，原来要自己回队列页翻。
 *
 * 数据全在 stores/notices（排队/计时）与 lib/notices（文案），本组件只画。
 * 位置用 `position: fixed`：外壳这一层没有 transform 祖先，fixed 不会被困住
 * （`.stage` 里那几个浮层就得靠 slot 才能 fixed，见 ViewerPage 的注释）。
 *
 * 琥珀橙是**刻意挑的非主题色**（令牌见 style.css 的同名注释）：青绿族是应用内一切常规
 * 元素的语言，这条提醒不该看起来像一个常规元素。成功与失败共用这一套色，成败看标题。
 *
 * 另一行是**连接状态**（`queue.reconnecting`）：队列 SSE 没有心跳帧，掉线是看不出来的，
 * 不说的话用户只会以为「任务还没跑完」。掉线期间不弹任务提醒，这条灰条是唯一的信号。
 * 出现条件是「有一次订阅尝试掉了下来」（`stores/queue._dropped`）—— 包括**一进页面就
 * 连不上**（后端没起、nginx 还没转发）：那时它说的是「还没接上」，比不吭声更接近事实，
 * 重连成功（`_onOpen`）才收起来。
 */
import { useRouter } from 'vue-router';
import { useNoticesStore } from '../stores/notices.js';
import { useQueueStore } from '../stores/queue.js';

const notices = useNoticesStore();
const queue = useQueueStore();
const router = useRouter();

/** 点整条 = 去队列页看这一行（失败原因、耗时、log_dir 都在那儿）。连着那条提醒一起
    收掉：人都到队列页了，屏上再挂着一条就是噪音。常驻的失败条同样按这个口径，
    「常驻」指的是**不会自己消失**，不是「点不掉」。 */
function openQueue(id: number): void {
  notices.dismiss(id);
  void router.push('/queue');
}
</script>

<template>
  <div class="jn-stack" role="status" aria-live="polite" data-e2e="job-notices">
    <div v-if="queue.reconnecting" class="jn jn-off" data-e2e="job-notice-offline">
      <span class="jn-title">任务提醒已断开</span>
      <span class="jn-text">正在重连后端队列（重连后自动校准列表）</span>
    </div>

    <div
      v-for="n in notices.items"
      :key="n.id"
      class="jn"
      :data-e2e="'job-notice-' + n.kind"
      :data-task-id="n.taskId"
    >
      <button type="button" class="jn-body" @click="openQueue(n.id)">
        <span class="jn-title">{{ n.title }}</span>
        <span v-if="n.text" class="jn-text">{{ n.text }}</span>
        <span v-if="n.reason" class="jn-reason">{{ n.reason }}</span>
        <span class="jn-go">去队列页 →</span>
      </button>
      <button
        v-if="n.persist"
        type="button"
        class="jn-x"
        aria-label="关闭这条提醒"
        :data-e2e="'job-notice-close-' + n.kind"
        @click="notices.dismiss(n.id)"
      >×</button>
    </div>
  </div>
</template>

<style scoped>
/* 右下方浮层。整层 pointer-events:none，只有卡片自己恢复 auto —— 空着的区域不该
   拦住底下的画布/列表（这条栈可能一直空着）。 */
.jn-stack {
  position: fixed;
  right: 18px;
  bottom: 18px;
  z-index: 40;                 /* 模态是 100：模态开着时这些条压在下面，不该抢焦点 */
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
  pointer-events: none;
}

.jn {
  pointer-events: auto;
  position: relative;
  display: flex;
  align-items: stretch;
  max-width: 360px;
  /* 左边那道橙条 = 提醒的身份（右下角一片白卡里一眼看出这是任务提醒） */
  border-left: 3px solid var(--notice-job);
  border-radius: var(--r-ctrl);
  background: var(--notice-job-bg);
  border-top: 1px solid var(--notice-job-line);
  border-right: 1px solid var(--notice-job-line);
  border-bottom: 1px solid var(--notice-job-line);
  box-shadow: var(--shadow-pop);
  animation: jn-in 0.16s ease-out;
}

@keyframes jn-in {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}

.jn-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  background: none;
  border: none;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}
.jn-title { font-size: 12.5px; font-weight: 700; color: var(--notice-job-ink); }
.jn-text { font-size: 11.5px; color: var(--ink-body); word-break: break-all; }
.jn-reason { font-size: 11px; color: var(--ink-sub); word-break: break-all; }
.jn-go { margin-top: 2px; font-size: 11px; color: var(--notice-job-ink); }
.jn-body:hover .jn-go { text-decoration: underline; }

.jn-x {
  flex: none;
  align-self: flex-start;
  width: 22px;
  height: 22px;
  margin: 4px 4px 0 0;
  padding: 0;
  border: none;
  border-radius: var(--r-ctrl);
  background: none;
  color: var(--ink-sub);
  font-family: inherit;
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
}
.jn-x:hover { background: rgba(138, 74, 24, 0.10); color: var(--notice-job-ink); }

/* 断线条：灰色（不是橙）—— 它不是任务结果，只是「这段时间我看不见了」 */
.jn-off {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-left: 3px solid var(--ink-faint);
}
.jn-off .jn-title { color: var(--ink-sub); }
.jn-off .jn-text { font-size: 11px; color: var(--ink-faint); }
</style>
