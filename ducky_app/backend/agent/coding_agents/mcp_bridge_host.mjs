/**
 * Host for UEFN MCP stdio on Windows: spawn the real bridge with windowsHide
 * so Cursor/Codex/Claude don't flash a console window for every tool call.
 *
 * Env:
 *   DUCKY_BRIDGE_ARGV — JSON array: [command, ...args]
 */
import { spawn } from "node:child_process";

const raw = process.env.DUCKY_BRIDGE_ARGV || "[]";
let argv;
try {
  argv = JSON.parse(raw);
} catch {
  console.error("DUCKY_BRIDGE_ARGV must be a JSON array");
  process.exit(1);
}
if (!Array.isArray(argv) || argv.length < 1) {
  console.error("DUCKY_BRIDGE_ARGV empty");
  process.exit(1);
}

const [command, ...args] = argv.map(String);
const child = spawn(command, args, {
  stdio: "inherit",
  windowsHide: true,
  env: process.env,
});
child.on("error", (err) => {
  console.error(String(err && err.stack ? err.stack : err));
  process.exit(1);
});
child.on("exit", (code, signal) => {
  if (signal) process.exit(1);
  process.exit(code == null ? 1 : code);
});
