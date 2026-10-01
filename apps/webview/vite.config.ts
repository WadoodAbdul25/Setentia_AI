import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  base: "./",
  build: {
    emptyOutDir: true,
    outDir: "../extension/media/webview",
    rollupOptions: {
      input: {
        flowMap: "flow-map.html",
        sidebar: "index.html",
      },
    },
    sourcemap: true,
  },
  plugins: [react()],
});
