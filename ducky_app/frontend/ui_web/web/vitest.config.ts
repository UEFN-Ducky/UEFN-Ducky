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
    // vitest's 5 s per-test default is a wall clock, not a check: a release run on a busy PC
    // (UEFN open, ~14 s to start each file) timed out tests that pass. Budgets stay in each test.
    testTimeout: 30_000,
    // Same for Testing Library's findBy*/waitFor (1 s default): see the setup file.
    setupFiles: ["./src/test/asyncTimeout.setup.ts"],
  },
});
