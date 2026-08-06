/// <reference types="vitest/config" />
import { fileURLToPath } from 'node:url';

import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [react()],
	resolve: {
		alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
	},
	server: {
		port: 5173,
		proxy: {
			// 开发期打后端；F02 起单测走 MSW，不依赖这个代理。
			'/api': {
				target: process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:3456',
				changeOrigin: true,
			},
		},
	},
	test: {
		environment: 'jsdom',
		globals: true,
		setupFiles: ['./src/test/setup.ts'],
		css: false,
		include: ['src/**/*.{test,spec}.{ts,tsx}'],
		// 显式声明而不是继承默认值：CI 机器比本地慢，行为要可预期。
		// 另：无限刷新循环这类 bug 的表现是挂死而非报红，超时就是兜底闸门。
		testTimeout: 10_000,
		hookTimeout: 10_000,
	},
});
