<script setup lang="ts">
// 阶段5 实现：共享任务队列（服务端唯一事实源 + 乐观更新 + SSE）
import { useQueueStore } from '../stores/queue';

const queue = useQueueStore();
</script>

<template>
  <div class="stub">
    <h2>任务队列</h2>
    <p v-if="queue.jobs.length === 0">
      占位页。阶段5 实现多人共享任务队列（REST 动作 + SSE 推送进度）。
    </p>
    <ul v-else>
      <li v-for="j in queue.jobs" :key="j.id">{{ j.id }} — {{ j.status }} — {{ j.progress }}%</li>
    </ul>
  </div>
</template>

<style scoped>
.stub {
  max-width: 640px;
  margin: 40px auto;
  text-align: center;
  color: #7f8c9b;
}
</style>
