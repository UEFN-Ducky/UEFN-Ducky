import { Icons } from "../../icons/Icons";
import { humanToolLabel } from "../../utils/agentActivity";
import type { ToolCategory } from "./toolCardTypes";
import { SkillBody } from "./bodies/SkillBody";
import { TerminalBody } from "./bodies/TerminalBody";
import { ScreenshotBody } from "./bodies/ScreenshotBody";
import { SearchBody } from "./bodies/SearchBody";
import { AskUserBody } from "./bodies/AskUserBody";
import { WalkthroughBody } from "./bodies/WalkthroughBody";

export type { ToolCardBodyProps, ToolCategory } from "./toolCardTypes";

const SEARCH_TOOLS = new Set([
  "search_assets",
  "list_assets",
  "find_devices",
  "search_verse_digest",
  "search_unreal_api",
  "workspace_list_dir",
  "Glob",
  "Grep",
  "ToolSearch",
  "web_search",
]);

const SCREENSHOT_TOOLS = new Set([
  "take_high_res_screenshot",
  "preview_asset",
]);

const SKILL_TOOLS = new Set(["uefn_skill", "skill_read_subskill", "Skill"]);

const PYTHON_TOOLS = new Set(["execute_python", "ai_generate_python"]);

const CODE_TOOLS = new Set([
  "Read",
  "Write",
  "Edit",
  "StrReplace",
  "ApplyPatch",
  "workspace_read_file",
  "workspace_write_file",
  "create_project_verse_file",
  "create_project_file",
]);

/** Mutating file tools — these stay as visible cards (inline diff). Reads fold into the accordion. */
const WRITE_TOOLS = new Set([
  "Write",
  "Edit",
  "StrReplace",
  "ApplyPatch",
  "workspace_write_file",
  "create_project_verse_file",
  "create_project_file",
]);

const VERSE_DIAG_TOOLS = new Set([
  "workspace_list_verse_errors",
  "workspace_compile_verse",
  "workspace_push_verse_changes",
  "workspace_open_verse_file",
]);

function nameIncludes(toolName: string, ...needles: string[]): boolean {
  const n = toolName.toLowerCase();
  return needles.some((needle) => n.includes(needle.toLowerCase()));
}

function isTerminalTool(name: string): boolean {
  return (
    name === "Bash" ||
    name === "PowerShell" ||
    name === "Shell" ||
    nameIncludes(name, "terminal", "bash", "powershell") ||
    name.startsWith("ducky_terminal")
  );
}

function isCodeTool(name: string): boolean {
  if (CODE_TOOLS.has(name)) return true;
  const n = name.toLowerCase();
  // Cursor SDK emits lowercase edit/write/read in some builds.
  if (n === "edit" || n === "write" || n === "read" || n === "strreplace" || n === "applypatch") {
    return true;
  }
  return nameIncludes(name, "workspace_read", "workspace_write");
}

function isFileWriteTool(name: string): boolean {
  if (WRITE_TOOLS.has(name)) return true;
  const n = name.toLowerCase();
  if (n === "edit" || n === "write" || n === "strreplace" || n === "applypatch") return true;
  return nameIncludes(name, "workspace_write", "create_project");
}

/**
 * Only file writes (inline diff) and ask-user stay as standalone rows.
 * Reads / search / bash / verse diagnostics fold into the "N tools" accordion
 * so they don't sit between chat bubbles.
 */
export function isStandaloneToolCard(toolName: string): boolean {
  return isFileWriteTool(toolName) || toolName === "ducky_ask_user";
}

/**
 * Ordered registry — first match wins.
 * Add a new styled tool type = push one entry here (+ optional Body + CSS block).
 *
 * Search is before verse so search_verse_digest stays a search card.
 * Search is before code for workspace_list_dir (directory scan, not a file edit).
 */
export const TOOL_CATEGORIES: ToolCategory[] = [
  {
    id: "ask_user",
    icon: () => <Icons.Chat />,
    label: () => "Clarify with user",
    match: (name) => name === "ducky_ask_user",
    Body: AskUserBody,
  },
  {
    id: "walkthrough",
    icon: () => <Icons.Sparkles />,
    label: () => "UI Tutorial",
    match: (name) => name === "ducky_walkthrough_run",
    Body: WalkthroughBody,
  },
  {
    id: "skill",
    icon: () => <Icons.BookOpen />,
    label: () => "Agent Knowledge Retrieval",
    match: (name) => SKILL_TOOLS.has(name) || name.toLowerCase() === "skill",
    Body: SkillBody,
  },
  {
    id: "terminal",
    icon: () => <Icons.Terminal />,
    label: (name) => {
      const n = name.toLowerCase();
      if (n.includes("powershell")) return "PowerShell";
      if (n === "bash" || n.includes("bash")) return "Bash Shell";
      return humanToolLabel(name);
    },
    match: isTerminalTool,
    Body: TerminalBody,
  },
  {
    id: "screenshot",
    icon: () => <Icons.Camera />,
    label: (name) => humanToolLabel(name),
    match: (name) => SCREENSHOT_TOOLS.has(name) || nameIncludes(name, "screenshot"),
    Body: ScreenshotBody,
  },
  {
    id: "python",
    icon: () => <Icons.Python />,
    label: (name) => humanToolLabel(name),
    match: (name) => PYTHON_TOOLS.has(name) || nameIncludes(name, "python"),
  },
  {
    id: "search",
    icon: () => <Icons.Search />,
    label: (name) => humanToolLabel(name),
    match: (name) =>
      SEARCH_TOOLS.has(name) ||
      nameIncludes(name, "search", "grep", "glob", "list_assets", "list_dir"),
    Body: SearchBody,
  },
  {
    id: "code",
    icon: () => <Icons.File />,
    label: (name) => {
      const n = name.toLowerCase();
      if (name === "workspace_write_file" || n === "write") return "Write file";
      if (name === "workspace_read_file" || n === "read") return "Read file";
      if (n === "edit" || n === "strreplace") return "Edit file";
      return humanToolLabel(name);
    },
    match: isCodeTool,
  },
  {
    id: "verse",
    icon: () => <Icons.Verse />,
    label: (name) => humanToolLabel(name),
    match: (name) => VERSE_DIAG_TOOLS.has(name) || nameIncludes(name, "verse"),
  },
  {
    id: "landscape",
    icon: () => <Icons.Mountain />,
    label: (name) => humanToolLabel(name),
    match: (name) => nameIncludes(name, "landscape", "terrain", "ground_z", "foliage"),
  },
  {
    id: "generic",
    icon: () => <Icons.Monitor />,
    label: (name) => humanToolLabel(name),
    match: () => true,
  },
];

const GENERIC = TOOL_CATEGORIES[TOOL_CATEGORIES.length - 1];

export function resolveToolCategory(toolName: string): ToolCategory {
  for (const cat of TOOL_CATEGORIES) {
    if (cat.id === "generic") continue;
    if (cat.match(toolName)) return cat;
  }
  return GENERIC;
}
