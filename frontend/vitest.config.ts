import { defineConfig } from 'vitest/config';

export default defineConfig({
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
