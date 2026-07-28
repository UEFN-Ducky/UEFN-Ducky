import type { SkillFile } from "../model/types";

export type LoadModeKind = "always" | "default" | "condition" | "demand";

export interface LoadModeBadge {
  kind: LoadModeKind;
  label: string;
  className: string;
}

/** Badge for a file's load behavior in the sidebar and details pane. */
export function fileLoadMode(
  file: SkillFile,
  packDefaultEnabled = false,
): LoadModeBadge {
  if (file.id === "core") {
    if (packDefaultEnabled) {
      return {
        kind: "default",
        label: "default on",
        className: "sps-file-badge is-default",
      };
    }
    return { kind: "demand", label: "on demand", className: "sps-file-badge" };
  }
  if (file.alwaysOn) {
    return { kind: "always", label: "always on", className: "sps-file-badge is-always" };
  }
  if (file.defaultEnabled) {
    return { kind: "default", label: "default on", className: "sps-file-badge is-default" };
  }
  if (file.loadCondition.trim()) {
    return {
      kind: "condition",
      label: "by condition",
      className: "sps-file-badge is-condition",
    };
  }
  return { kind: "demand", label: "on demand", className: "sps-file-badge" };
}
