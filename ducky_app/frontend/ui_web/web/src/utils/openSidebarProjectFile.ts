import { basename, isVerseFile, projectRelativePath } from "../verse-editor/utils/isVerseFile";

/** Open a sidebar project file in an editor tab (including binary previews). */
export function openSidebarProjectFile(
  path: string,
  name: string,
  openFileTab: (path: string, name: string) => void,
): void {
  const norm = projectRelativePath(path);
  const tabName = isVerseFile(norm) ? basename(norm) : name;
  openFileTab(norm, tabName);
}
