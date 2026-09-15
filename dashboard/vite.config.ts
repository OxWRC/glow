import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      $lib: path.resolve(__dirname, "src/lib"),
      $app: path.resolve(__dirname, "src/app"),
    },
  },
  server: {
    port: 3000,
    proxy: {
      "/api": {
        target: "http://api:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
