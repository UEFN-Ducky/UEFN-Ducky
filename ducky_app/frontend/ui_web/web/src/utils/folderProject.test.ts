import { afterEach, describe, expect, it, vi } from "vitest";
import {
  contentRootPath,
  isFolderProject,
  isLockedProjectFile,
  isWorkspaceLockedFile,
  projectRelativePath,
  registryKey,
  registryLookupKeys,
  setProjectContentRoot,
} from "../verse-editor/utils/isVerseFile";
import { ancestorDirPaths, fileMoveDest, parentDirPath, treePathDepth } from "./fileTreeDrag";
import { fileTreeCreateItems } from "./sidebarContextMenuItems";
import { resolvePickedFolder } from "../components/ProjectSelector";
import { parseAskUserQuestions } from "../ask-user/types";
import { classifyRichRef } from "../components/rich-content/classifyRichRef";
import { normalizeWorkspacePath } from "../components/rich-content/isWorkspacePath";

afterEach(() => setProjectContentRoot("Content"));

describe("island paths (unchanged)", () => {
  it("keeps the Content/ model", () => {
    setProjectContentRoot("Content");
    expect(isFolderProject()).toBe(false);
    expect(contentRootPath()).toBe("Content");
    expect(registryKey("Verse/a.verse")).toBe("content/verse/a.verse");
    expect(projectRelativePath("Verse/a.verse")).toBe("Content/Verse/a.verse");
    expect(isWorkspaceLockedFile("src/app.py")).toBe(true);
    expect(isWorkspaceLockedFile("Content/Verse/a.verse")).toBe(false);
    expect(isLockedProjectFile("Content/Python/init_unreal.py")).toBe(true);
    expect(parentDirPath("Content/Verse")).toBe("Content");
    expect(parentDirPath("Content")).toBe("__roots__");
    expect(treePathDepth("Content/Verse/a.verse")).toBe(3);
    expect(fileTreeCreateItems(vi.fn(), vi.fn(), vi.fn()).map((i) => i.id)).toEqual([
      "new-folder",
      "new-verse",
      "new-file",
    ]);
  });
});

describe("folder project paths", () => {
  it("uses plain root-relative paths under a '.' root", () => {
    setProjectContentRoot(".");
    expect(isFolderProject()).toBe(true);
    expect(contentRootPath()).toBe(".");
    expect(registryKey("src/App.tsx")).toBe("src/app.tsx");
    expect(registryKey("Content/notes.md")).toBe("content/notes.md");
    expect(registryLookupKeys("src/App.tsx")).toEqual(["src/app.tsx"]);
    expect(projectRelativePath("src/App.tsx")).toBe("src/App.tsx");
    expect(isWorkspaceLockedFile("src/app.py")).toBe(false);
    expect(isWorkspaceLockedFile(".")).toBe(false);
    expect(isWorkspaceLockedFile("ws:0")).toBe(true);
    expect(isLockedProjectFile("Content/Python/init_unreal.py")).toBe(false);
  });

  it("walks parents up to the '.' root", () => {
    setProjectContentRoot(".");
    expect(parentDirPath("src/pkg/app.py")).toBe("src/pkg");
    expect(parentDirPath("src")).toBe(".");
    expect(parentDirPath(".")).toBe("__roots__");
    expect(ancestorDirPaths("src/pkg/app.py")).toEqual(["src/pkg", "src", ".", "__roots__"]);
    expect(treePathDepth(".")).toBe(1);
    expect(treePathDepth("src/app.py")).toBe(3);
  });

  it("moves into the root when dropped on empty space", () => {
    setProjectContentRoot(".");
    expect(fileMoveDest("src/app.py", false, ".")).toBe(".");
    expect(fileMoveDest("README.md", false, ".")).toBeNull();
  });

  it("drops New Verse class from the create menu", () => {
    setProjectContentRoot(".");
    expect(fileTreeCreateItems(vi.fn(), vi.fn(), vi.fn()).map((i) => i.id)).toEqual(["new-folder", "new-file"]);
  });

  it("opens chat file refs as written", () => {
    setProjectContentRoot(".");
    expect(classifyRichRef("src/app.py").open).toEqual({ type: "file", path: "src/app.py" });
    expect(classifyRichRef("./README.md").open).toEqual({ type: "file", path: "README.md" });
    expect(normalizeWorkspacePath("docs/a.md")).toBe("docs/a.md");
    setProjectContentRoot("Content");
    expect(classifyRichRef("Verse/a.verse").open).toEqual({ type: "file", path: "Content/Verse/a.verse" });
  });
});

describe("resolvePickedFolder", () => {
  const confirm = vi.fn();
  const alert = vi.fn();
  afterEach(() => {
    confirm.mockReset();
    alert.mockReset();
  });

  it("opens a plain folder straight away", async () => {
    const api = { inspect_project_folder: vi.fn().mockResolvedValue({ ok: true, path: "C:/repo", kind: "folder", islands: [] }) };
    expect(await resolvePickedFolder(api, "C:/repo", confirm, alert)).toBe("C:/repo");
    expect(confirm).not.toHaveBeenCalled();
  });

  it("offers the one island inside a picked parent folder", async () => {
    const api = {
      inspect_project_folder: vi.fn().mockResolvedValue({
        ok: true,
        path: "C:/Fortnite Projects",
        name: "Fortnite Projects",
        kind: "folder",
        islands: [{ name: "Tycoony", path: "C:/Fortnite Projects/Tycoony" }],
      }),
    };
    confirm.mockResolvedValueOnce(true);
    expect(await resolvePickedFolder(api, "C:/Fortnite Projects", confirm, alert)).toBe("C:/Fortnite Projects/Tycoony");
    confirm.mockResolvedValueOnce("extra");
    expect(await resolvePickedFolder(api, "C:/Fortnite Projects", confirm, alert)).toBe("C:/Fortnite Projects");
    confirm.mockResolvedValueOnce(false);
    expect(await resolvePickedFolder(api, "C:/Fortnite Projects", confirm, alert)).toBeNull();
  });

  it("explains a folder Ducky can't open", async () => {
    const api = { inspect_project_folder: vi.fn().mockResolvedValue({ ok: false, error: "Pick a project folder, not a whole drive." }) };
    expect(await resolvePickedFolder(api, "C:/", confirm, alert)).toBeNull();
    expect(alert).toHaveBeenCalledWith(expect.objectContaining({ message: "Pick a project folder, not a whole drive." }));
  });

  it("falls back to the pick on an older backend", async () => {
    expect(await resolvePickedFolder({}, "C:/island", confirm, alert)).toBe("C:/island");
  });
});

describe("approval card questions", () => {
  it("keep the command detail and warning", () => {
    const [q] = parseAskUserQuestions([
      {
        id: "agent_permission",
        prompt: "Allow the agent to run this command?",
        detail: "git push origin main\n",
        warning: "Pushes commits to a remote.",
        options: [{ id: "once", label: "Allow once" }, { id: "deny", label: "Deny" }],
      },
    ]);
    expect(q?.detail).toBe("git push origin main");
    expect(q?.warning).toBe("Pushes commits to a remote.");
    const [plain] = parseAskUserQuestions([{ id: "a", prompt: "Pick", options: [{ id: "x", label: "X" }] }]);
    expect(plain).not.toHaveProperty("detail");
  });
});
