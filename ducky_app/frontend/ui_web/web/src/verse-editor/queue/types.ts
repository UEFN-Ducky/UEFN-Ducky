export type EditorActionType =
  | "open_file"
  | "scroll_to"
  | "highlight"
  | "delete_preview"
  | "insert_preview"
  | "apply_content"
  | "clear_decorations"
  | "set_vim_enabled"
  | "run_vim_command";

export type EditorActionStyle = "agent_cursor" | "selection" | "delete" | "insert";

export interface EditorRange {
  startLine: number;
  startCol: number;
  endLine: number;
  endCol: number;
}

export interface EditorAction {
  type: EditorActionType;
  path: string;
  range?: EditorRange;
  text?: string;
  style?: EditorActionStyle;
  durationMs?: number;
  /** Agent opens default to false — do not steal the active tab. */
  activate?: boolean;
  /** set_vim_enabled */
  enabled?: boolean;
  /** User-requested playback (replay button): runs even when follow-code is off/instant. */
  force?: boolean;
}

export interface EditorBatch {
  actions: EditorAction[];
  conv_id?: string;
}

export function parseEditorBatch(raw: unknown): EditorBatch | null {
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;
  const actions = obj.actions;
  if (!Array.isArray(actions)) return null;
  return {
    actions: actions as EditorAction[],
    conv_id: typeof obj.conv_id === "string" ? obj.conv_id : undefined,
  };
}
