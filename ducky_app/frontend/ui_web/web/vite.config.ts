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

// Raw ASCII control characters that charset "ascii" leaves in dependencies' string
// literals (Monaco's "\x01", xterm's C0 table), written as \u00XX: the same string, and
// the marketplace scan refuses the raw byte. One after a backslash is left for
// publish_panel.py's check to report (rewriting it would change its meaning).
// generateBundle: after the minifier, which writes the escape back as the raw byte.
const escapeControlChars = {
  name: "escape-control-chars",
  generateBundle(_: unknown, bundle: Record<string, { type: string; code?: string }>) {
    for (const chunk of Object.values(bundle)) {
      if (chunk.type !== "chunk" || !chunk.code) continue;
      const code = chunk.code;
      chunk.code = code.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, (c, at: number) =>
        code[at - 1] === "\\" ? c : "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0"),
      );
    }
  },
};

export default defineConfig(({ mode }) => ({
  define: { __PANEL_VERSION__: JSON.stringify(panelVersion()) },
  plugins: [react(), escapeControlChars],
  base: "./",
  esbuild: {
    // Every non-ASCII character as \uXXXX: the bundle ships as the phone panel inside
    // plugin-uefn-ducky, and the marketplace scan refuses raw zero-width / BOM / bidi
    // characters (dependencies carry them in strings and regexes).
    charset: "ascii",
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
      "/chat-attachments": "http://127.0.0.1:4199",
      "/duckies/custom": "http://127.0.0.1:4199",
      "/__fonts": "http://127.0.0.1:4199",
      "/__panel_event": "http://127.0.0.1:4199",
      "/__panel_boot": "http://127.0.0.1:4199",
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
    plugins: () => [escapeControlChars],
  },
}));
