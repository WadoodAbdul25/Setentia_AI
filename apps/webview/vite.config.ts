import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  base: "./",
  build: {
    emptyOutDir: true,
    outDir: "../extension/media/webview",
    sourcemap: true,
  },
  plugins: [react()],
});
