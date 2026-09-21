/**
 * 路由骨架（阶段2/4）：/viewer（查看器） /scenes（盘阵场景，阶段4）
 *   /chat（聊天） /queue（共享任务队列，阶段5）
 * - ViewerPage 阶段2 = 最小管线 demo；完整查看器 UI 组件化在阶段3。
 * - ScenesPage 阶段4 = 盘阵场景检索/打开（读服务器预生成 JPG）。
 * - ChatPage / QueuePage 为占位页，功能在阶段5（先写 REST/SSE 契约再写代码）。
 */
import { createRouter, createWebHistory } from 'vue-router';
import { APP_NAME } from '../lib/brand';

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/viewer' },
    {
      path: '/viewer',
      name: 'viewer',
      component: () => import('../pages/ViewerPage.vue'),
      // flush：该路由的页面贴边填满 app-main（去 12px 内衬），查看器不再出现浅色空框
      meta: { title: '查看器', flush: true },
    },
    {
      path: '/scenes',
      name: 'scenes',
      component: () => import('../pages/ScenesPage.vue'),
      meta: { title: '盘阵场景' },
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
    ? `${String(to.meta.title)} — ${APP_NAME}`
    : APP_NAME;
});

export default router;
