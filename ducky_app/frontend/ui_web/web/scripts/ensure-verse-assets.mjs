// Ensure the Verse language assets exist before Vite bundles them.
//
// UEFN-Ducky ships no Epic proprietary assets. The Verse grammar/snippets/theme are
// statically imported by src/verse-editor and must be present at build time, so this
// runs the Python extractor to pull them from the user's local UEFN install into the
// git-ignored src/verse-editor/lang/ directory. If they already exist, it does nothing.
//
// See docs/verse_extension.md and scripts/extract_vscode_ext.py.

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "../../../../.."); // web/scripts -> web -> ui_web -> frontend -> ducky_app -> repo
const langDir = resolve(here, "..", "src", "verse-editor", "lang");
const required = [
  "verse.tmGrammar.json",
  "language-configuration.json",
  "verse-snippets.json",
  "verse-dark.tmTheme.json",
];

const haveAll = required.every((f) => existsSync(resolve(langDir, f)));
if (haveAll) {
  process.exit(0);
}

console.log("[verse-assets] extracting Verse language assets from your UEFN install...");

const script = resolve(repoRoot, "scripts", "extract_vscode_ext.py");
const launchers = process.platform === "win32" ? ["py", "python", "python3"] : ["python3", "python"];

let ran = false;
for (const exe of launchers) {
  const res = spawnSync(exe, [script, "--lang-only"], { cwd: repoRoot, stdio: "inherit" });
  if (res.error && res.error.code === "ENOENT") continue; // launcher not found, try next
  ran = true;
  break;
}

if (!ran) {
  console.error("[verse-assets] no Python interpreter found (tried: " + launchers.join(", ") + ").");
}

const nowHaveAll = required.every((f) => existsSync(resolve(langDir, f)));
if (!nowHaveAll) {
  console.error(
    "[verse-assets] Verse language assets are missing and could not be extracted.\n" +
      "  Install UEFN, then run:  py scripts/extract_vscode_ext.py\n" +
      "  Or point at an extension: py scripts/extract_vscode_ext.py --extension <path-to-.vsix>",
  );
  process.exit(1);
}
