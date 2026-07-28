export interface SkillFile {
  id: string;
  /** Path inside the pack folder, e.g. "SKILL.md" or "references/tool_paths.md". */
  file: string;
  title: string;
  description: string;
  loadCondition: string;
  content: string;
  defaultEnabled: boolean;
  alwaysOn: boolean;
  /** ``user`` when created in Skills Studio; ``store`` / ``shipped`` / ``plugin`` otherwise. */
  origin: string;
}

export interface PackSummary {
  id: string;
  label: string;
  description: string;
  kind: string;
  version: number;
  license: string;
  author: string;
  copyright: string;
  homepage: string;
  contact: string;
  /** When false, archive is personal-use / no redistribute. Undefined for legacy packs. */
  allowRedistribute: boolean | null;
  /** Desktop plugin id when this pack ships inside a plugin. */
  sourcePluginId: string;
  /** ``store`` when installed from the Store catalog. */
  source: string;
  /** Store catalog slug for View in Store (usually pack id). */
  storeSlug: string;
  /** Pack-level origin stamp (``user`` for studio-created packs). */
  origin: string;
}

export interface PackWithFiles extends PackSummary {
  files: SkillFile[];
}

/** Sidebar / main-pane selection: pack folder or a file inside it. */
export type StudioSelection =
  | { kind: "pack"; packId: string }
  | { kind: "file"; packId: string; fileId: string };

/** Stable key for a file across packs (dirty tracking). */
export function fileKey(packId: string, fileId: string): string {
  return `${packId}:${fileId}`;
}

export function packSelection(packId: string): StudioSelection {
  return { kind: "pack", packId };
}

export function fileSelection(packId: string, fileId: string): StudioSelection {
  return { kind: "file", packId, fileId };
}

export function selectionEquals(a: StudioSelection | null, b: StudioSelection | null): boolean {
  if (!a || !b) return a === b;
  if (a.kind !== b.kind) return false;
  if (a.kind === "pack" && b.kind === "pack") return a.packId === b.packId;
  if (a.kind === "file" && b.kind === "file") {
    return a.packId === b.packId && a.fileId === b.fileId;
  }
  return false;
}

export function isPackSelected(selection: StudioSelection | null, packId: string): boolean {
  return selection?.kind === "pack" && selection.packId === packId;
}

export function isFileSelected(
  selection: StudioSelection | null,
  packId: string,
  fileId: string,
): boolean {
  return selection?.kind === "file" && selection.packId === packId && selection.fileId === fileId;
}
