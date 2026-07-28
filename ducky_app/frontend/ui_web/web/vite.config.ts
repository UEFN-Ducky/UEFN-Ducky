import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  optimizeDeps: {
    include: ["monaco-editor", "vscode-oniguruma", "vscode-textmate"],
  },
  assetsInclude: ["**/*.wasm"],
  server: {
    port: 5173,
    strictPort: true,
    // Plugin webview assets + panel event long-poll live on panel_httpd (:4199).
    proxy: {
      "/plugin-ui": "http://127.0.0.1:4199",
      "/user-sounds": "http://127.0.0.1:4199",
      "/__panel_events": "http://127.0.0.1:4199",
      "/__panel_event": "http://127.0.0.1:4199",
      "/__panel_run": "http://127.0.0.1:4199",
    },
  },
  worker: {
    format: "es",
  },
});
