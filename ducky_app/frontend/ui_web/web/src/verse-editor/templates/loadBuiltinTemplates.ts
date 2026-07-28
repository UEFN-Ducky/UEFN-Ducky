import type { VerseTemplate, VerseTemplateMeta } from "./types";

const metaModules = import.meta.glob("./builtin/*/meta.json", { eager: true }) as Record<
  string,
  { default: VerseTemplateMeta }
>;

const contentModules = import.meta.glob("./builtin/*/template.verse", {
  eager: true,
  query: "?raw",
  import: "default",
}) as Record<string, string>;

function folderFromPath(path: string): string {
  const match = path.match(/\/builtin\/([^/]+)\//);
  return match?.[1] ?? "";
}

function sortBuiltinTemplates(templates: VerseTemplate[]): VerseTemplate[] {
  return [...templates].sort((a, b) => {
    if (a.id === "new") return -1;
    if (b.id === "new") return 1;
    return a.name.localeCompare(b.name);
  });
}

export function loadBuiltinTemplates(): VerseTemplate[] {
  const byFolder = new Map<string, Partial<VerseTemplate>>();

  for (const [path, mod] of Object.entries(metaModules)) {
    const folder = folderFromPath(path);
    if (!folder) continue;
    const meta = mod.default;
    byFolder.set(folder, {
      id: meta.id,
      name: meta.name,
      icon: meta.icon,
      description: meta.description,
      kind: "builtin",
    });
  }

  for (const [path, content] of Object.entries(contentModules)) {
    const folder = folderFromPath(path);
    if (!folder) continue;
    const existing = byFolder.get(folder) ?? { kind: "builtin" as const };
    byFolder.set(folder, { ...existing, content });
  }

  const templates: VerseTemplate[] = [];
  for (const partial of byFolder.values()) {
    if (!partial.id || !partial.name || !partial.icon || partial.content === undefined) continue;
    templates.push({
      id: partial.id,
      name: partial.name,
      icon: partial.icon,
      description: partial.description,
      content: partial.content,
      kind: "builtin",
    });
  }

  return sortBuiltinTemplates(templates);
}
