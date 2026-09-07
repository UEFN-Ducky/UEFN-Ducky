// Production build of the app + the chat perf harness page (dev/measurement only).
import { defineConfig, mergeConfig } from "vite";
import base from "./vite.config";

export default mergeConfig(
  base,
  defineConfig({
    build: {
      outDir: "dist-perf",
      emptyOutDir: true,
      rollupOptions: { input: { main: "index.html", perf: "perf-chat.html" } },
    },
  }),
);
