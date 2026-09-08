import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const monacoStub = fileURLToPath(new URL("./src/test/monacoStub.ts", import.meta.url));

export default defineConfig({
  resolve: {
    alias: [
      // Monaco's browser entry points and `?worker` imports do not resolve outside
      // a browser build, which made any test whose import graph merely reached the
      // Verse editor fail to collect. Stylesheets are excluded: Vite handles those.
      { find: /^monaco-editor(?!.*\.css$).*/, replacement: monacoStub },
    ],
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
