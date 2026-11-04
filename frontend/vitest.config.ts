import { defineConfig } from 'vitest/config';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'node',
    // lib 纯函数 + store 归并纯函数（阶段5 chat/queue）；setup 同用 FileReader shim
    include: [
      'src/lib/__tests__/**/*.test.ts',
      'src/stores/__tests__/**/*.test.ts',
    ],
    setupFiles: ['src/lib/__tests__/setup.ts'],
    testTimeout: 60000,
    hookTimeout: 60000,
  },
});
