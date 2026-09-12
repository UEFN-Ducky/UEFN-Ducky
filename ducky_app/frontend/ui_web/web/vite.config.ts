import { defineConfig } from "vite";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";

function panelVersion(): string {
  try {
    const init = readFileSync(fileURLToPath(new URL("../../__init__.py", import.meta.url)), "utf8");
    const m = /__version__\s*=\s*"([^"]+)"/.exec(init);
    return m ? m[1] : "0.0.0";
  } catch {
    return "0.0.0";
  }
}

export default defineConfig(({ mode }) => ({
  define: { __PANEL_VERSION__: JSON.stringify(panelVersion()) },
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
      "/model-files": "http://127.0.0.1:4199",
      "/tool-captures": "http://127.0.0.1:4199",
      "/duckies/custom": "http://127.0.0.1:4199",
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
