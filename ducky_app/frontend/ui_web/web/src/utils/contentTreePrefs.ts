const CONTENT_ALL_PROJECTS_KEY = "uefn-panel-content-all-projects";

export function readContentAllProjects(): boolean {
  try {
    return localStorage.getItem(CONTENT_ALL_PROJECTS_KEY) === "1";
  } catch {
    return false;
  }
}

export function rememberContentAllProjects(allProjects: boolean): void {
  try {
    localStorage.setItem(CONTENT_ALL_PROJECTS_KEY, allProjects ? "1" : "0");
  } catch {
    /* ignore */
  }
}
