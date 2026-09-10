#!/usr/bin/env node
/** Refuse to ship debug ingest / noisy console helpers in the Store EXE. */
import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = path.join(root, "src");
const viteConfig = path.join(root, "vite.config.ts");
const shipPy = path.join(root, "..", "webview_ship.py");

function rg(pattern, dir) {
  try {
    return execSync(
      `rg -n -F --glob "!*.test.*" ${JSON.stringify(pattern)} ${JSON.stringify(dir)}`,
      {
        encoding: "utf8",
        stdio: ["pipe", "pipe", "pipe"],
      },
    );
  } catch (err) {
    if (err.status === 1) return "";
    throw err;
  }
}

let failed = false;
function ban(pattern, dir, label) {
  const hit = rg(pattern, dir);
  if (hit.trim()) {
    console.error(`Ship gate: ${label}\n${hit}`);
    failed = true;
  }
}

ban("127.0.0.1:7248", src, "debug ingest host must not be in src/");
ban("X-Debug-Session-Id", src, "debug session header must not be in src/");
ban("#region agent log", src, "agent-log debug regions must not ship");
ban("[chat-list]", src, "chat-list console probe must not ship");

const vite = fs.readFileSync(viteConfig, "utf8");
if (!vite.includes('"console.log"') || !vite.includes("pure:")) {
  console.error("Ship gate: vite.config.ts must drop console.log/info/debug in production");
  failed = true;
}

const ship = fs.readFileSync(shipPy, "utf8");
if (!ship.includes("AreDefaultContextMenusEnabled = False")) {
  console.error("Ship gate: packaged WebView2 must disable native context menus");
  failed = true;
}

if (failed) process.exit(1);
console.log("Ship gate: no debug ingest, production console drop, native right-click off");
