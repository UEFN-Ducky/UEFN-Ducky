const DUCKIES_ALL_PROJECTS_KEY = "uefn-panel-duckies-all-projects";

export function readDuckiesAllProjects(): boolean {
  try {
    return localStorage.getItem(DUCKIES_ALL_PROJECTS_KEY) === "1";
  } catch {
    return false;
  }
}

export function rememberDuckiesAllProjects(allProjects: boolean): void {
  try {
    localStorage.setItem(DUCKIES_ALL_PROJECTS_KEY, allProjects ? "1" : "0");
  } catch {
    /* ignore */
  }
}
