import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/lib/__tests__/**/*.test.ts'],
    setupFiles: ['src/lib/__tests__/setup.ts'],
    testTimeout: 60000,
    hookTimeout: 60000,
  },
});
