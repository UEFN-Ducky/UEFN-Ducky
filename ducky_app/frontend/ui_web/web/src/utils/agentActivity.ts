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
  workspace_read_files: "Read files",
  workspace_file_outline: "File outline",
  workspace_tree: "Folder tree",
  workspace_search: "Search File Content",
  workspace_find: "Find files",
  workspace_edit_file: "Edit file",
  workspace_multi_edit: "Edit file",
  workspace_replace_lines: "Edit file",
  workspace_move_file: "Move file",
  workspace_delete_file: "Delete file",
  workspace_git: "Git",
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
  web_fetch: "Reading a page",
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

// What a shell command is doing, in a few words: the status line never shows the raw
// command (`"C:\Windows\...\powershell.exe" -Command ...`); the tool card still has it.
const SHELL_ACTIVITY: Array<[RegExp, string]> = [
  [/\b(pytest|vitest|jest|mocha|cargo\s+test|go\s+test|(npm|pnpm|yarn)\s+(run\s+)?test|unittest|playwright\s+test)\b/i, "Running tests"],
  [/\bgit\s+(commit|merge|rebase|cherry-pick)\b/i, "Committing"],
  [/\bgit\s+push\b/i, "Pushing"],
  [/\bgit\s+(pull|fetch|clone)\b/i, "Fetching from git"],
  [/\bgit\s+\w/i, "Checking git"],
  [/\b(rg|grep|findstr|select-string|ag)\b/i, "Searching files"],
  [/\b(npm|pnpm|yarn|pip|uv|cargo)\s+(install|add|ci|sync)\b/i, "Installing packages"],
  [/\b(tsc|vite\s+build|cargo\s+(build|check)|(npm|pnpm|yarn)\s+(run\s+)?build|msbuild|dotnet\s+build|make|cmake|pyinstaller)\b/i, "Building"],
  [/\b(eslint|ruff|mypy|prettier|clippy|lint)\b/i, "Checking code"],
  [/\b(get-content|gc|cat|type|head|tail|less|more|awk)\b|\bsed\s+-n\b/i, "Reading files"],
  [/\b(get-childitem|gci|ls|dir|tree|find)\b/i, "Listing files"],
  [/\b(curl|wget|invoke-webrequest|invoke-restmethod|iwr|irm)\b/i, "Fetching from the web"],
  [/\b(sqlite3|psql|mysql)\b/i, "Querying a database"],
  [/\b(python|py|node|deno|bun)\b/i, "Running a script"],
];

export function shellActivityText(command: string, description?: unknown): string {
  const said = typeof description === "string" ? description.trim() : "";
  if (said) return said.length > 80 ? `${said.slice(0, 77)}…` : said;
  // Look past the wrapper (`powershell.exe -Command "..."`, `bash -lc '...'`) at what it runs.
  const inner = command.replace(/^\s*"?[^"\s]*(powershell|pwsh|bash|cmd|sh)(\.exe)?"?\s+(-NoProfile\s+)?(-Command|-c|-lc|\/c)\s+/i, "");
  for (const [rx, label] of SHELL_ACTIVITY) {
    if (rx.test(inner)) return label;
  }
  return "Running a command";
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
    const command = args.command ?? args.cmd;
    if (typeof command === "string" && command.trim()) {
      return shellActivityText(command, args.description);
    }
    if (Array.isArray(command) && command.length) {
      return shellActivityText(command.map(String).join(" "), args.description);
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
