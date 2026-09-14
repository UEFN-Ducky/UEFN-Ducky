import { describe, expect, it } from "vitest";
import { contentTreeVisibleEntries, isProjectContentRoot } from "./contentTreeProjects";
import type { ProjectFileEntry } from "../types/panel";

function entry(partial: Partial<ProjectFileEntry> & Pick<ProjectFileEntry, "name" | "path">): ProjectFileEntry {
  return { is_dir: true, ...partial };
}

describe("contentTreeVisibleEntries", () => {
  const contentRoot = entry({ name: "ExampleProject1", path: "ws:0", read_only: false, kind: "content" });
  const verse = entry({ name: "Verse", path: "Content/Verse" });
  const other = entry({ name: "Roguelike", path: "abs:C:/islands/Roguelike/Content", read_only: true, kind: "project" });

  it("shows Content children when All projects is off", () => {
    expect(
      contentTreeVisibleEntries({
        allProjects: false,
        contentRoot,
        contentChildren: [verse],
        projectRoots: [other],
      }),
    ).toEqual([verse]);
  });

  it("nests the current island and other project folders when All projects is on", () => {
    expect(
      contentTreeVisibleEntries({
        allProjects: true,
        contentRoot,
        contentChildren: [verse],
        projectRoots: [other],
      }),
    ).toEqual([contentRoot, other]);
  });

  it("isProjectContentRoot matches kind=project only", () => {
    expect(isProjectContentRoot(other)).toBe(true);
    expect(isProjectContentRoot(contentRoot)).toBe(false);
  });
});
