import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// 开发环境把 /api 代理到后端；生产由 nginx 反代
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_PROXY_TARGET || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
