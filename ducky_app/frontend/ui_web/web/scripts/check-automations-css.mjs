#!/usr/bin/env node
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const css = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "../src/theme/styles/automations.css"),
  "utf8",
);
const hex = css.match(/#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b/);
const rgb = css.match(/rgba?\(/);
if (hex || rgb) {
  console.error("automations.css must use theme tokens only; found", hex?.[0] || rgb?.[0]);
  process.exit(1);
}
console.log("automations.css: tokens only");
