import type { ChatMessage } from "../types/panel";
import { unwrapCodingAgentTool } from "./unwrapCodingAgentTool";

export interface ActivityLine {
  id: string;
  text: string;
  status: "pending" | "success" | "error" | "streaming" | "thinking";
}

export function turnStartIndex(messages: ChatMessage[]): number {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "user") return i + 1;
  }
  return 0;
}

export function splitTurnMessages(messages: ChatMessage[], inFlight: boolean) {
  if (!inFlight) {
    return { committed: messages, turnMessages: [] as ChatMessage[] };
  }
  const start = turnStartIndex(messages);
  return {
    committed: messages.slice(0, start),
    turnMessages: messages.slice(start),
  };
}

const TOOL_ACTIVITY_LABELS: Record<string, string> = {
  workspace_read_file: "Read file",
  workspace_write_file: "Write file",
  workspace_list_verse_errors: "Verse errors",
  workspace_list_dir: "List Directories",
  workspace_compile_verse: "Compile Verse Workspace",
  create_verse_device: "Create Verse Device",
  list_verse_devices: "List Verse Devices",
  ducky_get_local_project: "Reading local project",
  project_memory_list: "Listing project memory",
  project_memory_get: "Pulling project memory",
  project_memory_save: "Saving project memory",
  project_memory_append: "Updating project memory",
  project_memory_delete: "Deleting memory entry",
  ducky_memory_overview: "Surveying ducky memories",
  ping: "Checking UEFN listener",
  inspect_verse_device: "Inspecting Verse device",
  get_all_actors: "Query Actors",
  find_devices: "Finding devices",
  search_assets: "Search Asset Registry",
  execute_python: "Editor Python Script",
  save_current_level: "Save Level",
  take_high_res_screenshot: "Capture Screenshot",
  create_landscape: "Generate Landscape",
  sculpt_landscape: "Sculpt Terrain",
  get_ground_z: "Raycast Ground Height",
  set_viewport_camera: "Move Editor Camera",
  spawn_actor: "Spawn Actor in Level",
  get_level_bounds: "Level Bounds",
  Skill: "Agent Knowledge Retrieval",
  read: "Read file",
  edit: "Edit file",
  write: "Write file",
  Glob: "Glob Scan",
  Grep: "Search File Content",
  ls: "List Directories",
  grep: "Search File Content",
  glob: "Glob Scan",
  semSearch: "Semantic Search",
  shell: "Bash Shell",
  ToolSearch: "Tool Registry Search",
  Bash: "Bash Shell",
  PowerShell: "PowerShell",
  file_change: "Editing files",
  web_search: "Searching the web",
};

export function formatToolDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "0ms";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/** Group feed: "Rigging Ducky · Inspecting Verse device". */
export function prefixSpeaker(author: ChatMessage["author"] | null | undefined, text: string): string {
  const name = author?.name?.trim();
  const t = (text || "").trim();
  if (!name || !t) return t;
  return `${name} · ${t}`;
}

export function humanToolLabel(toolName: string): string {
  const bare = toolName.replace(/^mcp__uefn__/i, "").replace(/^mcp__[^_]+__/i, "");
  return TOOL_ACTIVITY_LABELS[toolName] ?? TOOL_ACTIVITY_LABELS[bare] ?? bare.replace(/_/g, " ");
}

function toolLineText(msg: ChatMessage): string {
  const rawArgs = msg.tool?.arguments ?? {};
  const unwrapped = unwrapCodingAgentTool(
    msg.tool?.name ?? "",
    rawArgs && typeof rawArgs === "object" && !Array.isArray(rawArgs)
      ? (rawArgs as Record<string, unknown>)
      : {},
  );
  const name = unwrapped.name;
  const args = unwrapped.arguments;
  if (name) {
    const label = humanToolLabel(name);
    const path = args.relative_path ?? args.path;
    if (typeof path === "string" && path.trim()) {
      return `${label} · ${path.trim().replace(/\\/g, "/")}`;
    }
    const command = args.command;
    if (typeof command === "string" && command.trim()) {
      const short = command.trim().length > 60 ? `${command.trim().slice(0, 57)}…` : command.trim();
      return `${label} · ${short}`;
    }
    const query = args.query ?? args.q;
    if (typeof query === "string" && query.trim()) {
      return `${label} · ${query.trim()}`;
    }
    return label;
  }
  const raw = msg.text?.trim();
  if (raw) return raw.replace(/^⚙\s*/, "");
  return "Running tool";
}

const THINKING_SNIPPET_MAX = 140;
const THINKING_SNIPPET_SCAN_MAX = 800;

function thinkingSnippet(text: string): string {
  // This runs on every reasoning delta. Scan only the tail instead of repeatedly
  // normalizing an arbitrarily large accumulated thought process.
  const tail = text.length > THINKING_SNIPPET_SCAN_MAX ? text.slice(-THINKING_SNIPPET_SCAN_MAX) : text;
  const trimmed = tail.trim().replace(/\s+/g, " ");
  if (trimmed.length <= THINKING_SNIPPET_MAX) return trimmed;
  return `…${trimmed.slice(-(THINKING_SNIPPET_MAX - 1))}`;
}

export function buildActivityLines(
  turnMessages: ChatMessage[],
  streamBuffer: string,
  streamThinking = "",
): ActivityLine[] {
  const lines: ActivityLine[] = [];

  for (let i = 0; i < turnMessages.length; i++) {
    const msg = turnMessages[i];
    if (msg.role !== "tool") continue;

    const next = turnMessages[i + 1];
    const hasResult = next && (next.role === "success" || next.role === "error");
    lines.push({
      id: String(msg.id),
      text: prefixSpeaker(msg.author, toolLineText(msg)),
      status: hasResult ? (next.role === "success" ? "success" : "error") : "pending",
    });
    if (hasResult) i++;
  }

  const stream = streamBuffer.trim();
  if (stream) {
    lines.push({ id: "stream", text: stream, status: "streaming" });
  } else {
    const thinking = streamThinking.trim();
    if (thinking) {
      lines.push({ id: "thinking", text: thinkingSnippet(thinking), status: "thinking" });
    }
  }

  return lines;
}

export function activityPanelTitle(
  lines: ActivityLine[],
  isWaitingOnLinked: boolean,
  waitingCount: number,
  waitingTitle?: string,
  statusText?: string,
): string {
  if (isWaitingOnLinked) {
    return waitingCount === 1 && waitingTitle
      ? `Waiting for ${waitingTitle}`
      : `Waiting for ${waitingCount} linked chats`;
  }
  if (lines.length === 0) {
    const status = (statusText || "").trim();
    return status || "Thinking";
  }
  const pending = lines.find((line) => line.status === "pending");
  if (pending) return pending.text;
  if (lines.some((line) => line.status === "thinking")) return "Thinking";
  if (lines.some((line) => line.status === "streaming")) return "Writing…";
  const last = lines[lines.length - 1];
  return last?.text ?? "Thought briefly";
}
