import type { PackWithFiles, SkillFile } from "../skill-pack-studio/model/types";

/** In-memory skill packs list — survives Settings tab switches (same session). */
export type SkillPacksCatalogCache = {
  packs: PackWithFiles[];
  contentLoadedPackIds: Record<string, boolean>;
};

let memory: SkillPacksCatalogCache | null = null;

export function peekSkillPacksCatalogCache(): SkillPacksCatalogCache | null {
  return memory;
}

export function rememberSkillPacksCatalog(
  packs: PackWithFiles[],
  contentLoadedPackIds: Record<string, boolean>,
): void {
  memory = {
    packs,
    contentLoadedPackIds: { ...contentLoadedPackIds },
  };
}

/** Keep markdown bodies for a pack after the user opens it. */
export function rememberSkillPackFiles(packId: string, files: SkillFile[]): void {
  if (!memory) {
    memory = {
      packs: [],
      contentLoadedPackIds: { [packId]: true },
    };
    return;
  }
  const hasPack = memory.packs.some((p) => p.id === packId);
  memory = {
    packs: hasPack
      ? memory.packs.map((p) => (p.id === packId ? { ...p, files } : p))
      : memory.packs,
    contentLoadedPackIds: { ...memory.contentLoadedPackIds, [packId]: true },
  };
}

export function invalidateSkillPacksCatalogCache(): void {
  memory = null;
}
