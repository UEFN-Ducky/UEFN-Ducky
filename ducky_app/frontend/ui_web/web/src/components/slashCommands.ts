import { getApi } from "../hooks/usePanelApi";
import type { AgentMode } from "../types/panel";

/** What the composer knows about itself — decides which commands are offered. */
export interface SlashCommandScope {
  codingAgent: string;
  isGroup: boolean;
}

export interface SlashCommandContext {
  chatId: string;
  /** Everything typed after the command name, trimmed. */
  argument: string;
  setAgentMode: (mode: AgentMode) => void;
  openModelPicker: () => void;
  captureSnip: () => void;
  showHelp: () => void;
  /** Transient status line under the composer. */
  report: (message: string) => void;
}

export interface SlashCommand {
  /** Command name without the leading slash. */
  name: string;
  /** Argument hint shown next to the name, e.g. "<goal>". */
  args?: string;
  description: string;
  /** Extra terms the filter matches on, so "/objective" still finds /goal. */
  keywords?: string[];
  /** Refuse to run with an empty argument, and keep the menu hint visible. */
  requiresArgument?: boolean;
  /** Omitted means the command is offered for every agent and chat kind. */
  isAvailable?: (scope: SlashCommandScope) => boolean;
  run: (ctx: SlashCommandContext) => void | Promise<void>;
}

/** Modes and the model picker live in the solo-chat toolbar only. */
const soloChatOnly = (scope: SlashCommandScope) => !scope.isGroup;

function modeCommand(mode: AgentMode, description: string): SlashCommand {
  return {
    name: mode,
    description,
    keywords: ["mode"],
    isAvailable: soloChatOnly,
    run: ({ setAgentMode, report }) => {
      setAgentMode(mode);
      report(`Mode set to ${mode}.`);
    },
  };
}

async function createTask(
  ctx: SlashCommandContext,
  title: string,
  goal: string,
): Promise<void> {
  const api = getApi();
  if (!api?.create_task) {
    ctx.report("Tasks are unavailable — the panel API did not expose create_task.");
    return;
  }
  try {
    const task = await api.create_task(title, goal, [ctx.chatId]);
    const created = String(task?.title || title);
    ctx.report(`Task created: ${created}`);
  } catch (err) {
    ctx.report(`Could not create the task: ${String(err)}`);
  }
}

/** First sentence or first 60 characters — a task title, not a paragraph. */
function titleFromGoal(goal: string): string {
  const firstLine = goal.split("\n")[0].trim();
  if (firstLine.length <= 60) return firstLine;
  return `${firstLine.slice(0, 57).trimEnd()}…`;
}

export const SLASH_COMMANDS: SlashCommand[] = [
  modeCommand("agent", "Let the ducky edit files and run tools."),
  modeCommand("plan", "Have the ducky draft a plan before touching code."),
  modeCommand("ask", "Read-only answers — no edits, no tools."),
  {
    name: "goal",
    args: "<goal>",
    description: "Track an objective for this chat as a task.",
    keywords: ["objective", "task"],
    requiresArgument: true,
    run: (ctx) => createTask(ctx, titleFromGoal(ctx.argument), ctx.argument),
  },
  {
    name: "task",
    args: "<title>",
    description: "Create a task linked to this chat.",
    keywords: ["todo"],
    requiresArgument: true,
    run: (ctx) => createTask(ctx, ctx.argument, ""),
  },
  {
    name: "model",
    description: "Pick the model this chat runs on.",
    keywords: ["llm", "agent"],
    isAvailable: soloChatOnly,
    run: ({ openModelPicker }) => openModelPicker(),
  },
  {
    name: "snip",
    description: "Capture a region of the screen into the composer.",
    keywords: ["screenshot", "capture"],
    isAvailable: () => Boolean(getApi()?.snip_screen),
    run: ({ captureSnip }) => captureSnip(),
  },
  {
    name: "help",
    description: "List every command available here.",
    keywords: ["commands"],
    run: ({ showHelp }) => showHelp(),
  },
];

export function commandsForScope(scope: SlashCommandScope): SlashCommand[] {
  return SLASH_COMMANDS.filter((cmd) => cmd.isAvailable?.(scope) ?? true);
}

export interface ComposerPlaceholderInput {
  liveVoiceMuted: boolean;
  isGroup: boolean;
  groupEmpty: boolean;
  agentRunning: boolean;
  noModelsAvailable: boolean;
  modelLabel: string;
}

/** Idle composers mention `/` so the command palette is discoverable. */
export function composerPlaceholder(p: ComposerPlaceholderInput): string {
  if (p.liveVoiceMuted) return "You're muted — type to chat (Shift+Enter for newline)";
  if (p.isGroup) {
    if (p.groupEmpty) return "Invite a ducky above to start the roundtable…";
    if (p.agentRunning) return "Add a follow-up… (Shift+Enter for newline)";
    return "Message the group… (Shift+Enter for newline)  ·  / for commands";
  }
  if (p.noModelsAvailable) {
    return "No models available — use the button below to open Settings → LLMs";
  }
  if (p.agentRunning) return "Add a follow-up… (Shift+Enter for newline)";
  return `Ask ${p.modelLabel}... (Shift+Enter for newline)  ·  / for commands`;
}

/**
 * The command name being typed, or null when the composer is not in that state.
 * Only a leading slash counts, and only until the first space — once the user
 * starts on the argument the menu gets out of the way.
 */
export function readSlashQuery(text: string): string | null {
  const match = /^\/([a-z0-9_-]*)$/i.exec(text);
  return match ? match[1] : null;
}

export function filterCommands(commands: SlashCommand[], query: string): SlashCommand[] {
  const q = query.trim().toLowerCase();
  if (!q) return commands;
  const matches = commands.filter(
    (cmd) =>
      cmd.name.startsWith(q) ||
      cmd.keywords?.some((kw) => kw.toLowerCase().startsWith(q)),
  );
  // Prefix hits on the name itself rank above keyword-only hits.
  return matches.sort((a, b) => {
    const an = a.name.startsWith(q) ? 0 : 1;
    const bn = b.name.startsWith(q) ? 0 : 1;
    return an - bn || a.name.localeCompare(b.name);
  });
}

/** A fully typed command line, ready to run instead of being sent to the ducky. */
export function matchSlashCommand(
  text: string,
  commands: SlashCommand[],
): { command: SlashCommand; argument: string } | null {
  const match = /^\/([a-z0-9_-]+)(?:[ \t]+([\s\S]*))?$/i.exec(text.trim());
  if (!match) return null;
  const name = match[1].toLowerCase();
  const command = commands.find((cmd) => cmd.name === name);
  if (!command) return null;
  return { command, argument: (match[2] ?? "").trim() };
}
