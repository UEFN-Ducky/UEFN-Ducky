import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  base: "./",
  esbuild: {
    drop: mode === "production" ? ["debugger"] : [],
    pure: mode === "production" ? ["console.log", "console.info", "console.debug"] : [],
  },
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
      // Remote View from :5173 (dev viewer): RPC + WebRTC signaling socket.
      "/__panel_api": {
        target: "http://127.0.0.1:4199",
        changeOrigin: true,
        headers: { Origin: "http://127.0.0.1:4199" },
      },
      "/__window_stream": { target: "ws://127.0.0.1:4199", ws: true, changeOrigin: true },
    },
  },
  worker: {
    format: "es",
  },
}));
