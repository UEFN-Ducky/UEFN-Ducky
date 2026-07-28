import type { EditorTab } from "../types/panel";
import { basename, isVerseFile, projectRelativePath } from "../verse-editor/utils/isVerseFile";

export interface OpenProjectFileOptions {
  line?: number;
  column?: number;
  activate?: boolean;
}

type OpenFileTab = (path: string, name: string, options?: { activate?: boolean }) => void;

type RevealFile = (path: string, line: number, column: number) => void;

/** Open a project file tab and reveal a line when the editor is ready. */
export function openProjectFileAt(
  path: string,
  name: string,
  openFileTab: OpenFileTab,
  reveal: RevealFile | undefined,
  options?: OpenProjectFileOptions,
): void {
  const norm = projectRelativePath(path);
  const tabName = isVerseFile(norm) ? basename(norm) : name;
  const line = options?.line ?? 1;
  const column = options?.column ?? 1;
  openFileTab(norm, tabName, { activate: options?.activate ?? true });
  reveal?.(norm, line, column);
}

export function focusActivatedEditorTab(
  tab: EditorTab,
  reveal: RevealFile | undefined,
): void {
  if (tab.kind === "file" && tab.path) {
    reveal?.(projectRelativePath(tab.path), 1, 1);
  }
}
