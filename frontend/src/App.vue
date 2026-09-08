<script setup lang="ts">
// 应用外壳：顶部导航（查看器 / 盘阵场景 / 聊天 / 任务队列）+ 路由视图
import { useRoute, RouterLink, RouterView } from 'vue-router';

const route = useRoute();
</script>

<template>
  <div class="app-shell">
    <header class="app-nav">
      <RouterLink to="/viewer" class="brand">sr_agent_platform</RouterLink>
      <nav class="nav-links">
        <RouterLink to="/viewer">查看器</RouterLink>
        <RouterLink to="/scenes">盘阵场景</RouterLink>
        <RouterLink to="/chat">聊天</RouterLink>
        <RouterLink to="/queue">任务队列</RouterLink>
      </nav>
    </header>
    <!-- flush 路由（查看器）去掉 padding，页面贴边填满 -->
    <main class="app-main" :class="{ flush: route.meta.flush }">
      <RouterView />
    </main>
  </div>
</template>
