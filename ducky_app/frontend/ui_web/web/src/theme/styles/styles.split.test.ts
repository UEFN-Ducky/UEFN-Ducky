import { readFileSync } from "node:fs";
import { expect, it } from "vitest";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");
const VIEWS: [css: string, view: string, importPath: string][] = [
  ["settings", "../../views/SettingsView.tsx", "../theme/styles/settings.css"],
  ["automations", "../../automations/AutomationsView.tsx", "../theme/styles/automations.css"],
  ["changes", "../../components/changes/ChangesView.tsx", "../../theme/styles/changes.css"],
];

it("boot loads only the shared part of each lazy view's stylesheet", () => {
  const index = read("./index.css");
  for (const [name] of VIEWS) {
    expect(index).not.toContain(`"./${name}.css"`);
    expect(index).toContain(`"./${name}-shared.css"`);
  }
});

it("each lazy view imports its own stylesheet", () => {
  for (const [, view, importPath] of VIEWS) {
    expect(read(view)).toContain(`import "${importPath}";`);
  }
});

it("both halves hold real rules", () => {
  for (const [name] of VIEWS) {
    expect(read(`./${name}.css`).length).toBeGreaterThan(4000);
    expect(read(`./${name}-shared.css`).length).toBeGreaterThan(1000);
  }
});
