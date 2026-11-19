import react from '@vitejs/plugin-react';
import { realpathSync } from 'node:fs';
import { searchForWorkspaceRoot } from 'vite';
import { defineConfig } from 'vitest/config';

// `VITE_API_PORT` trỏ dev server vào backend chạy ở cổng khác (worktree song song); mặc định 8990.
const BACKEND = `http://127.0.0.1:${process.env.VITE_API_PORT || 8990}`;

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Worktree: node_modules là junction tới repo chính → cho phép đọc file (font) theo đường dẫn thật.
    fs: { allow: [searchForWorkspaceRoot(process.cwd()), realpathSync('node_modules')] },
    proxy: {
      // changeOrigin: Host thành 127.0.0.1:8990 nên backend coi dev server là "Máy nhà" như khi mở thẳng cổng 8990.
      // SSE (/api/events) đi qua cùng proxy.
      // headers.origin: backend chỉ tin Origin loopback đúng cổng của nó (CSRF) — dev server khác cổng nên đổi Origin.
      '/api': { target: BACKEND, changeOrigin: true, headers: { origin: BACKEND } },
      '/media': { target: BACKEND, changeOrigin: true, headers: { origin: BACKEND } },
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    restoreMocks: true,
  },
});
