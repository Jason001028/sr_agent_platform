<script setup lang="ts">
// 应用外壳：顶部导航（查看器 / 聊天 / 任务队列）+ 路由视图
import { RouterLink, RouterView } from 'vue-router';
</script>

<template>
  <div class="app-shell">
    <header class="app-nav">
      <RouterLink to="/viewer" class="brand">sr_agent_platform</RouterLink>
      <nav class="nav-links">
        <RouterLink to="/viewer">查看器</RouterLink>
        <RouterLink to="/chat">聊天</RouterLink>
        <RouterLink to="/queue">任务队列</RouterLink>
      </nav>
    </header>
    <main class="app-main">
      <RouterView />
    </main>
  </div>
</template>
