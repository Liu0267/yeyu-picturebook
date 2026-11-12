import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
// 开发态：5173 代理到交互服务 8801（同源相对路径，无需CORS）
export default defineConfig({
    plugins: [react()],
    server: {
        port: 5173,
        strictPort: true,
        proxy: {
            '/v1': 'http://127.0.0.1:8801',
            '/media': 'http://127.0.0.1:8801',
        },
    },
    build: {
        outDir: 'dist',
        sourcemap: false,
    },
});
