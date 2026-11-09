// Identical synthetic burst through the actual before/after TypeScript modules.
// Counts diagnostic records and JSON bytes; does not measure application RAM.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const web = join(root, "ducky_app/frontend/ui_web/web");
const ts = createRequire(join(web, "package.json"))("typescript");
const relative = "ducky_app/frontend/ui_web/web/src/hooks/perfMonitor.ts";
const revision = process.argv[2] || "HEAD";
const before = execFileSync("git", ["show", `${revision}:${relative}`], { cwd: root, encoding: "utf8" });

function measure(source) {
  const batches = [];
  let tick;
  let observer;
  class Observer {
    static supportedEntryTypes = ["long-animation-frame"];
    constructor(callback) { observer = callback; }
    observe() {}
  }
  const api = { report_ui_perf: (batch) => batches.push(batch) };
  const exports = {};
  const code = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(code, {
    exports,
    window: { PerformanceObserver: Observer, addEventListener() {} },
    require: (name) => {
      if (name === "./usePanelApi") return { getApi: () => api };
      if (name === "../utils/visibleInterval") return { setVisibleInterval: (fn) => { tick = fn; return () => {}; } };
      throw new Error(`Unexpected import: ${name}`);
    },
  }, { timeout: 5000 });
  exports.installPerfMonitor();
  observer({ getEntries: () => [{ name: "longtask", duration: 320 }] });
  for (let depth = 1; depth <= 290; depth++) exports.notePendingDepth(depth);
  tick();
  const batch = batches[0];
  return {
    diagnostic_records: batch.length,
    json_bytes: Buffer.byteLength(JSON.stringify(batch)),
    stall_preserved: batch.some((row) => row.kind === "ui_stall" && row.duration_ms === 320),
    queue_records: batch.filter((row) => row.name === "pending_events").length,
    queue_observations_in_summary: batch.find((row) => row.name === "pending_events")?.samples ?? null,
    peak_pending: Math.max(...batch.map((row) => row.peak_pending ?? 0)),
  };
}

console.log(JSON.stringify({
  before_commit: execFileSync("git", ["rev-parse", revision], { cwd: root, encoding: "utf8" }).trim(),
  scenario: "one 320 ms stall, then a queue growing from 1 to 290, then one diagnostic flush",
  before: measure(before),
  after: measure(readFileSync(join(root, relative), "utf8")),
}, null, 2));
