<script setup lang="ts">
// 应用外壳：顶部导航（查看器 / 场景库 / 聊天 / 任务队列）+ 路由视图
import { ref } from 'vue';
import { useRoute, RouterLink, RouterView } from 'vue-router';
import { APP_NAME } from './lib/brand';

const route = useRoute();

// 品牌 logo：素材不进仓库，部署后在服务器上换 `<APP>/dist/logo.png` 即可（见 brand.ts）。
// 没有这份文件时 <img> 触发 error，这里把它摘掉，退回到 .brand-mark 里那颗占位小方块 ——
// 预留区宽度不随有无 logo 变化，所以平台名不会左右跳。摘掉后不再重试，避免刷请求：
// 服务器上补了 logo 要硬刷一次页面。
// 用 BASE_URL 拼而不是写死 /logo.png：与 vite base:'./' 同一条规则，产物解压到子目录也跟得上。
const logoSrc = `${import.meta.env.BASE_URL}logo.png`;
const logoOk = ref(true);
</script>

<template>
  <div class="app-shell">
    <header class="app-nav">
      <RouterLink to="/viewer" class="brand" :class="{ 'has-logo': logoOk }">
        <span class="brand-mark">
          <img
            v-if="logoOk"
            class="brand-logo"
            :src="logoSrc"
            alt=""
            draggable="false"
            @error="logoOk = false"
          />
        </span>
        <span class="brand-name">{{ APP_NAME }}</span>
      </RouterLink>
      <nav class="nav-links">
        <RouterLink to="/viewer">查看器</RouterLink>
        <RouterLink to="/scenes">场景库</RouterLink>
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
