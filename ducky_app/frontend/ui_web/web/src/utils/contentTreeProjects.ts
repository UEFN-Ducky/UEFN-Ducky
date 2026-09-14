import type { ProjectFileEntry } from "../types/panel";

export function isProjectContentRoot(entry: ProjectFileEntry): boolean {
  return entry.kind === "project";
}

/** Duckies All projects: wrap the current Content tree under the island name. */
export function contentTreeVisibleEntries(args: {
  allProjects: boolean;
  contentRoot: ProjectFileEntry | null;
  contentChildren: ProjectFileEntry[];
  projectRoots: ProjectFileEntry[];
}): ProjectFileEntry[] {
  if (!args.allProjects) return args.contentChildren;
  const current = args.contentRoot ? [args.contentRoot] : [];
  return [...current, ...args.projectRoots];
}
