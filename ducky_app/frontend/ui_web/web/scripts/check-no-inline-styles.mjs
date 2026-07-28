#!/usr/bin/env node
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = path.join(root, "src");

const patterns = [
  "style=\\{\\{",
  "style=\\{",
  "currentTarget\\.style",
];

let failed = false;
for (const pattern of patterns) {
  try {
    execSync(`rg -n "${pattern}" "${src}"`, { stdio: "pipe" });
    console.error(`Found forbidden pattern: ${pattern}`);
    failed = true;
  } catch {
    // rg exits 1 when no matches — expected
  }
}

if (failed) {
  process.exit(1);
}

console.log("No inline style props found in src/");
