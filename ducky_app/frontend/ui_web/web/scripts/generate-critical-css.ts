import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { defaultAppearanceCssBlock } from "../src/theme/tokenEngine.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const out = join(root, "src/theme/styles/critical.css");
const content =
  "/* Flash prevention — generated from defaultTokens via scripts/generate-critical-css.ts */\n" +
  defaultAppearanceCssBlock() +
  "\n";
writeFileSync(out, content);
console.log("Wrote critical.css");
