#!/usr/bin/env node
/** After vite build: dist must not contain debug ingest or noisy log tags. */
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const dist = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "dist");

function rg(pattern) {
  try {
    return execSync(`rg -n -F ${JSON.stringify(pattern)} ${JSON.stringify(dist)}`, {
      encoding: "utf8",
      stdio: ["pipe", "pipe", "pipe"],
    });
  } catch (err) {
    if (err.status === 1) return "";
    throw err;
  }
}

let failed = false;
for (const [pattern, label] of [
  ["127.0.0.1:7248", "debug ingest"],
  ["X-Debug-Session-Id", "debug session"],
  ["console helpers active", "verse-editor boot log"],
  ["[chat-list] window", "chat-list window log"],
]) {
  const hit = rg(pattern);
  if (hit.trim()) {
    console.error(`Ship dist gate: ${label} leaked into dist/\n${hit.slice(0, 800)}`);
    failed = true;
  }
}

if (failed) process.exit(1);
console.log("Ship dist gate: production bundle is clean");
