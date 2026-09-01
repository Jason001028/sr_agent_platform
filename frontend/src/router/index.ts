/**
 * 路由骨架（阶段2）：/viewer（查看器） /chat（聊天） /queue（共享任务队列）
 * - ViewerPage 阶段2 = 最小管线 demo；完整查看器 UI 组件化在阶段3。
 * - ChatPage / QueuePage 为占位页，功能在阶段5（先写 REST/SSE 契约再写代码）。
 */
import { createRouter, createWebHistory } from 'vue-router';

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/viewer' },
    {
      path: '/viewer',
      name: 'viewer',
      component: () => import('../pages/ViewerPage.vue'),
      meta: { title: '查看器' },
    },
    {
      path: '/chat',
      name: 'chat',
      component: () => import('../pages/ChatPage.vue'),
      meta: { title: '聊天' },
    },
    {
      path: '/queue',
      name: 'queue',
      component: () => import('../pages/QueuePage.vue'),
      meta: { title: '任务队列' },
    },
  ],
});

router.afterEach((to) => {
  document.title = to.meta.title
    ? `${String(to.meta.title)} — sr_agent_platform`
    : 'sr_agent_platform';
});

export default router;
