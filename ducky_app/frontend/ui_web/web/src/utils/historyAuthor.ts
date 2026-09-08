import type { FileHistoryEntry } from "../types/panel";
import { shortModelLabel } from "../components/groupMemberHover";

export type HistoryAuthorKind = "you" | "revert" | "ducky";

export interface HistoryAuthor {
  kind: HistoryAuthorKind;
  /** Badge text: "You", "Reverted", or the ducky's name. */
  label: string;
  /** Tooltip: model, tool, coding agent — whatever is known. */
  detail: string;
}

type AuthorFields = Pick<FileHistoryEntry, "source" | "ducky_name" | "model" | "tool" | "coding_agent">;

const AGENT_LABELS: Record<string, string> = {
  claude_code: "Claude Code",
  codex: "Codex",
  cursor: "Cursor",
};

/** Who wrote a history version. Legacy entries (no attribution) read as "You". */
export function historyAuthor(entry: AuthorFields): HistoryAuthor {
  const source = (entry.source || "").trim();
  if (source === "revert") {
    return { kind: "revert", label: "Reverted", detail: "Restored by a changeset revert" };
  }
  if (source !== "agent") {
    return { kind: "you", label: "You", detail: "Saved from the editor" };
  }
  const name = (entry.ducky_name || "").trim() || "AI";
  const bits: string[] = [];
  if (entry.model) bits.push(shortModelLabel(entry.model));
  const agent = (entry.coding_agent || "").trim();
  if (agent && agent !== "ducky") bits.push(AGENT_LABELS[agent] ?? agent);
  if (entry.tool) bits.push(entry.tool);
  return { kind: "ducky", label: name, detail: bits.length ? `${name} · ${bits.join(" · ")}` : `Written by ${name}` };
}
