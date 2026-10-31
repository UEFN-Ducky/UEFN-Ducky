export type AgentMode = "ask" | "plan" | "agent";


export type ChatLayoutMode = "full" | "sidebarHidden";

/** The header's left button toggles what is on screen: the pane shows only when the layout
 *  mode and the dock both say open, and a fresh install starts it closed with the mode "full". */
export function nextChatLayoutMode(mode: ChatLayoutMode, leftRailOpen: boolean): ChatLayoutMode {
  return mode !== "sidebarHidden" && leftRailOpen ? "sidebarHidden" : "full";
}

export interface InstallInfo {
  installed: boolean;
  install_location: string | null;
  install_scope: "user" | "machine" | null;
  registry_version: string | null;
  uninstall_command: string | null;
  quiet_uninstall_command: string | null;
}

export interface AppUpdateStatus {
  local_version: string;
  remote_version: string | null;
  update_available: boolean;
  installed: boolean;
  /** installed = Inno production; portable = loose EXE; dev = UEFN-Ducky-Dev-* */
  channel?: "installed" | "portable" | "dev";
  install_location: string | null;
  install_scope: "user" | "machine" | null;
  installer_url: string | null;
  installer_sha256: string | null;
  release_notes: string | null;
  versions?: DuckyOSStorePatchNote[];
  download_url: string;
  /** none | no_release | up_to_date | update_available | paused | error */
  feed_status?: string;
  /** The feed offers an update pack: installed copies update in the background. */
  update_pack?: boolean;
  error: string | null;
}

/** Background self-update (frontend/update_swap.py). */
export type AutoUpdatePhase = "off" | "idle" | "checking" | "downloading" | "ready" | "switching" | "failed" | "updated";

export interface AutoUpdateStatus {
  phase: AutoUpdatePhase;
  version: string;
  done?: number;
  total?: number;
  error?: string;
  /** Why it hasn't switched yet (phase ready). */
  waiting_for?: string[];
  /** The version it switched from (phase updated). */
  previous?: string;
}

/** A window's word on being in use, for the switch's idle check. */
export interface UpdateUiReport {
  window: string;
  visible: boolean;
  idle_s: number;
  dirty: number;
  jobs: string[];
}

export interface SavedAgentPermissionDto {
  conv_id: string;
  title: string;
  rule: string;
  label: string;
}

/** One login or API key of a gateway (backend.agent.accounts). `default` is today's key / CLI login. */
export interface GatewayAccountDto {
  id: string;
  label: string;
  kind: "key" | "login";
  active: boolean;
  /** Epoch seconds the limit resets, or null when usable. */
  exhausted_until: number | null;
  /** Logins: the CLI's answer, null while it is still being asked. */
  status?: GatewayAccountStatusDto | null;
  /** Keys: a key is stored for this account. */
  has_key?: boolean;
  /** Keys: masked key, e.g. `sk-…a1b2`, when the backend sends one. */
  key_hint?: string;
}

export interface GatewayAccountStatusDto {
  loggedIn: boolean;
  email?: string;
  plan?: string;
}

/** One usage window of an account (5-hour, weekly, a key's rate limit). */
export interface UsageWindowDto {
  id: string;
  label: string;
  used: number;
  limit: number;
  unit?: string;
  reset?: string;
  readout?: string;
}

export type GatewayAccountResult<T = object> = ({ ok: true } & T) | { ok: false; error: string };

/** A chat's approval mode: ask before changes, accept edits (default), allow everything. */
export type ChatPermissionModeId = "ask" | "edits" | "all";

export interface ChatPermissionModeDto {
  id: ChatPermissionModeId;
  label: string;
  description: string;
  available: boolean;
  /** Why it can't be picked here (empty when available). */
  reason: string;
}

export interface ChatPermissionsDto {
  mode: ChatPermissionModeId;
  label: string;
  /** False when Allow everything comes from the chat that started this run. */
  own: boolean;
  from_title: string;
  agent: string;
  agent_label: string;
  /** False when the agent never asks for approval inside Ducky (its modes are all unavailable). */
  asks: boolean;
  /** For an agent that never asks: what it does instead ("Codex edits files … without asking"). */
  note?: string;
  /** For an agent that never asks: which agents on this PC do ask. */
  hint?: string;
  /** Set when a mode is missing because the agent's plugin is too old. */
  update?: { plugin_id: string; label: string; version: string; from_store: boolean } | null;
  modes: ChatPermissionModeDto[];
  rules: { rule: string; label: string }[];
}

export interface UpdaterResult {
  ok: boolean;
  error: string | null;
  stage: string;
}

export interface UpdateProgress {
  stage: string;
  downloaded_bytes: number;
  total_bytes: number;
  error: string | null;
}

export interface DuckyDto {
  id: string;
  label: string;
  file: string;
  kind: "builtin" | "custom";
  url?: string;
}

export interface DuckyCatalogDto {
  builtin: DuckyDto[];
  custom: DuckyDto[];
  default_style: string;
  custom_base_url?: string;
}

export interface VerseTemplateFileDto {
  path: string;
  content: string;
}

export interface VerseTemplateDto {
  id: string;
  name: string;
  icon: string;
  content: string;
  kind: "custom";
  folder?: string;
  files?: VerseTemplateFileDto[];
}

export interface GroupMemberObservation {
  observed_at: number;
  runtime: "running" | "idle" | "unknown";
  group_id: string;
  project_slug: string | null;
  role: "leader" | "member" | "unknown";
  assignment: { plan_id: string; chat_id: string; node_id: string | null; title: string; status: string } | null;
}

export interface GroupMemberDto {
  observation?: GroupMemberObservation;
  member_conv_id: string;
  profile_id: string;
  /** Role / profile handle (unique for @mentions). */
  name: string;
  /** Duck identity label (Artist, Hacker, …). */
  ducky_name?: string;
  ducky_style?: string;
  /** Qualified backend:model for the member's turns. */
  model?: string;
  coding_agent?: string;
  tts_voice?: string;
  tts_speed?: number;
  color?: string;
  /** Nested group hub — one representative speaks for the subgroup. */
  is_group?: boolean;
  /** Write lane: null/omitted = unrestricted, [] = read-only, else gitignore-style globs. */
  write_allowed?: string[] | null;
  lane_set_by?: string;
  lane_set_at?: number;
}

export interface MessageAuthorDto {
  name: string;
  member_conv_id?: string;
  tts_voice?: string;
  tts_speed?: number;
  color?: string;
  profile_id?: string;
  /** Nested-group labels, outer to inner (e.g. ["Art Team", "Rigging Swarm"]). */
  group_path?: string[];
}

export interface ChatTab {
  id: string;
  name: string;
  duckyStyle?: string;
  duckyName?: string;
  /** Stable library agent-profile id — prefer over duckyName for identity. */
  profileId?: string;
  duckyPersonality?: string;
  ttsVoice?: string;
  ttsSpeed?: number;
  sortOrder?: number;
  updated?: number;
  filePath?: string;
  model?: string;
  provider?: string;
  codingAgent?: string;
  thinkingEffort?: string;
  terminalSessionId?: string;
  /** Parent group hub id when this chat is a group member. */
  parentConvId?: string;
  /** Multi-ducky group chat tab. */
  isGroup?: boolean;
  /** @deprecated Subagents retired — always false. Kept for call-site compat. */
  isSubagent?: boolean;
  /** Group hub: designated leader member_conv_id. */
  leaderConvId?: string;
  /** True when this chat is the leader of its parent group hub. */
  isLeader?: boolean;
  groupMembers?: GroupMemberDto[];
  toolCallCount?: number;
  fileCount?: number;
  /** Latest context-window size (tokens) for sidebar group hover totals. */
  contextTokens?: number;
  /** Home project slug when the Duckies tree is showing all projects. */
  projectSlug?: string;
  /** Home project name for the archive list. */
  projectName?: string;
  /** Collapsed to a progress row in the sidebar and the team bar; the agent keeps working. */
  muted?: boolean;
}

export type EditorTabKind =
  | "chat"
  | "file"
  | "terminal"
  | "plan"
  | "usage"
  | "settings"
  | "discord"
  | "plugin"
  | "verse-translated"
  | "ducky-profile"
  | "changes"
  | "workflows";

export interface EditorTab {
  id: string;
  kind: EditorTabKind;
  name: string;
  path?: string;
  chatId?: string;
  /** For plan tabs: explicit project root ("" = app-data). Omit = active project. */
  projectRoot?: string;
  duckyStyle?: string;
  /** Group hub tab — kept on the tab so open works even before hubChats loads. */
  isGroup?: boolean;
  /** VS Code "preview" tab: opened by a single sidebar click, rendered italic, and
   * reused in place when the next file/chat is single-clicked. Cleared (pinned) when
   * the user edits the file, double-clicks the tab, or double-clicks the sidebar row. */
  preview?: boolean;
  terminalSessionId?: string;
  terminalShell?: "bash" | "powershell";
  terminalWsUrl?: string;
  terminalCwd?: string;
  /** The ducky (chat) that opened this terminal; empty for the person's own. */
  terminalOwner?: string;
}

export function chatTabId(chatId: string): string {
  return `chat:${chatId}`;
}

export function fileTabId(relativePath: string): string {
  // Lowercased: Windows paths are case-insensitive, and the same file reaches here
  // with on-disk casing (file tree) or lowercase registry keys (Problems panel) —
  // both must resolve to ONE tab, not duplicates.
  return `file:${relativePath.replace(/\\/g, "/").toLowerCase()}`;
}

export function terminalTabId(sessionId: string): string {
  return `terminal:${sessionId}`;
}

export function planTabId(chatId: string): string {
  return `plan:${chatId}`;
}

/** Per-provider usage dashboard tab (`usage:<providerId>`). */
export function usageTabId(providerId: string): string {
  return `usage:${providerId.trim().toLowerCase()}`;
}

export function usageProviderIdFromTab(tabId: string): string {
  return tabId.startsWith("usage:") ? tabId.slice(6) : "";
}

/** Singleton Settings editor tab (VS Code–style). */
export function settingsTabId(): string {
  return "settings:main";
}

/** Singleton Changes editor tab: one project-wide ledger, never one per chat. */
export function changesTabId(): string {
  return "changes:main";
}

/** Singleton Workflows editor tab. */
export function workflowsTabId(): string {
  return "workflows:main";
}

/** Tab ids saved by older builds (separate Automations and Pipelines tabs). */
export function isLegacyWorkflowsTabId(id: string): boolean {
  return id === "automations:main" || id === "pipelines:main" || id.startsWith("automations:") || id.startsWith("pipelines:");
}

export interface AutomationGraphNodeDto {
  width?: number;
  /** Its own title-bar (and wire) color: red, amber, green, blue or purple; none = by kind. */
  color?: string;
  /** Locked: it can't be moved, wired or changed until unlocked. */
  locked?: boolean;
  /** Its own icon (an emoji) picked in its details; none = the icon of its kind. */
  icon?: string;
  id: string;
  type: string;
  x: number;
  y: number;
  config: Record<string, unknown>;
  label?: string;
  description?: string;
}

export interface AutomationGraphEdgeDto {
  source: string;
  target: string;
  /** main | true | false | each | done (white wires) or data (a value from an output pin to an input pin). */
  kind: string;
  source_pin?: string;
  target_pin?: string;
}

/** What a pin carries; wires only join pins whose types fit (automations/pins.ts). */
export type PinType = "text" | "number" | "boolean" | "json" | "any" | "image" | "images" | "audio" | "video" | "mesh" | "svg" | "pdf" | "file";
export interface PinDto {
  id: string;
  label: string;
  type: PinType;
  required?: boolean;
  default?: unknown;
  description?: string;
}
/** A picked file as it travels on a wire. */
export interface FileRefDto {
  kind: string;
  path: string;
  name: string;
  /** Loopback link the editor shows it through (thumbnails, Last run). */
  url?: string;
  /** Made by a generator: which one, its task (Meshy edits reuse it) and its web copy. */
  provider?: string;
  backend?: string;
  task_id?: string;
  stage?: string;
  remote?: string;
}

/** One way an image / 3D node can run: a plugin tool, its cost, ready here or why not. */
export interface AutomationBackendSetupDto {
  /** install / enable the plugin (its Store page) or add the gateway's API key (Settings > LLMs). */
  kind: "install" | "enable" | "key";
  /** openPanelRoute route and item: settings.store + plugin slug, settings.llms + provider id. */
  route: string;
  item: string;
  /** The button's words ("Add your Google API key"). */
  label: string;
}

export interface AutomationBackendDto {
  id: string;
  label: string;
  /** The installed plugin's own name for itself. */
  plugin: string;
  /** The plugin's id (what default_image_provider / default_mesh_provider save). */
  plugin_id?: string;
  credits: number;
  /** "~5 credits", "Your own API key" on an AI gateway, or the plugin's own words ("Ducky AI credit"). */
  cost?: string;
  available: boolean;
  /** Why it can't run here: install, turn on, or add the key. */
  reason?: string;
  /** Where to fix it: a panel route (+ item) for the details' button and its Show me. */
  setup?: AutomationBackendSetupDto;
  /** Controls declared by the direct image handler (its image models) or a plugin's image tool (size, quality). */
  config_fields?: AutomationFieldDto[];
  model?: string;
  /** An AI gateway billed to the person's own API key. */
  own_key?: boolean;
  /** Spends even at 0 credits (billed to an account), so it needs the Spend credits switch. */
  paid?: boolean;
  /** What runs when nothing is picked: the person's default maker, else the first set up. */
  default?: boolean;
}

export interface AutomationGraphGroupDto {
  id: string;
  name: string;
  /** Nodes directly in this box; a node sits in its innermost group only. */
  node_ids: string[];
  /** The group this box sits inside (nested groups). */
  parent_id?: string;
  /** Box colour: red, amber, green, blue or purple; none is the plain box. */
  color?: string;
  /** Locked: the box and everything in it can't be moved or changed until unlocked. */
  locked?: boolean;
  /** Its own icon (an emoji) for the title and details; none = a box. */
  icon?: string;
}

export interface AutomationGraphDto {
  groups?: AutomationGraphGroupDto[];
  nodes: AutomationGraphNodeDto[];
  edges: AutomationGraphEdgeDto[];
}

export interface AutomationRunStepDto {
  ok?: boolean;
  id?: string;
  type?: string;
  label?: string;
  error?: string;
  result?: unknown;
  branch?: boolean;
  /** Run workflow nodes: the steps of the workflow it ran. */
  substeps?: AutomationRunStepDto[];
  /** Run workflow nodes: it has Return nodes and none was reached, so this path ended. */
  stop?: boolean;
  /** The input values it ran with (long text cut, files as file refs). */
  inputs?: Record<string, unknown>;
  /** Custom code steps: what ducky.log wrote (the last 16 KB). */
  log?: string;
  /** Custom code steps that threw: where in the code, and why. */
  code_error?: CodeErrorDto;
}

/** Custom code nodes (type "code.js"): a problem the check found, 1-based line and column. */
export interface CodeProblemDto {
  line: number;
  col: number;
  message: string;
  severity: "error" | "warning";
}

/** Where a custom code step failed (its line in the node's code). */
export interface CodeErrorDto {
  line?: number;
  col?: number;
  message: string;
}

/** The pins a custom code node declares (kept from its last good check). */
export interface CodePinsDto {
  exec: boolean;
  inputs: PinDto[];
  outputs: PinDto[];
}

/** The MCP tools and built-in node types a custom code node may call. */
export interface CodeUsesDto {
  tools: string[];
  builtins: string[];
}

/** check_workflow_node_code: the code's declaration parsed, and what is wrong with it. */
export interface CodeCheckDto {
  ok: boolean;
  error?: string;
  problems: CodeProblemDto[];
  node?: Record<string, unknown> | null;
  pins: CodePinsDto;
  settings_spec: AutomationFieldDto[];
  uses: CodeUsesDto;
  code_sha: string;
}

/** A custom code node made from a built-in: which one, and its settings when it was converted. */
export interface CodeBasedOnDto {
  type: string;
  config: Record<string, unknown>;
  code_sha?: string;
}

/** get_workflow_node_code: the JavaScript a node runs (a built-in's is generated from its settings). */
export interface WorkflowNodeCodeDto {
  ok?: boolean;
  error?: string;
  kind: "builtin" | "custom" | "flow";
  code: string;
  code_sha: string;
  based_on?: CodeBasedOnDto | null;
  /** The built-in's generated code it was made from, for Compare. */
  based_on_code?: string;
  pins: CodePinsDto;
  settings_spec: AutomationFieldDto[];
  uses: CodeUsesDto;
  problems: CodeProblemDto[];
  /** Whether Edit as custom code can turn it into a custom code node; `reason` says why not, or what changes. */
  convertible: boolean;
  reason: string;
  /** Its code may run unattended: a person ran it, saved it, or reviewed it. */
  approved: boolean;
  /** The inputs of its latest recorded run, for Test. */
  last_inputs?: Record<string, unknown>;
}

/** test_workflow_node: one run of a custom code node's (unsaved) code. */
export interface CodeTestResultDto {
  ok: boolean;
  outputs?: Record<string, unknown>;
  log?: string;
  tool_calls?: Array<{ name: string; args?: unknown; dry_run?: boolean }>;
  error?: CodeErrorDto | null;
  ms?: number;
  /** It did not run: an agent changed the saved code and nobody reviewed it yet. */
  needs_review?: boolean;
}

/** workflow_code_api: the `ducky` object's types and docs, for the code editor. */
export interface WorkflowCodeApiDto {
  ok?: boolean;
  error?: string;
  /** declare module "ducky" { ... } */
  dts: string;
  manifest?: Array<{ name: string; ts: string; doc: string; async?: boolean }>;
  blank?: string;
  declaration_schema?: unknown;
  examples?: unknown;
  tools_dts?: string;
}

export interface AutomationRunDto {
  /** Unique execution id, separate from the workflow id. */
  run?: string;
  ok?: boolean;
  error?: string;
  id?: string;
  steps?: AutomationRunStepDto[];
  conv_id?: string;
  started?: number;
  ended?: number;
  trigger_id?: string;
  /** The run picker's name for it (a run_title field or return), e.g. "Grey Wolf". */
  title?: string;
  /** A Run workflow step's run: the run that called it. */
  parent?: { workflow: string; run: string };
  /** Values its Return nodes handed back. */
  outputs?: Record<string, unknown>;
  /** Each node's output pin values from this run (long text cut, files as file refs). */
  node_outputs?: Record<string, Record<string, unknown>>;
}

/** Who owns a workflow: Local (this PC only) or a shared space (synced to everyone in it). */
export interface WorkflowOwnerDto {
  id: string;
  kind: "local" | "shared";
  label: string;
  state?: string;
  readOnly?: boolean;
  /** Why it is read-only here (no permission, waiting for the space's data, …). */
  reason?: string;
  /** How a shared owner is named (the provider's word for it), and what its paused line says. */
  kindLabel?: string;
  pausedLabel?: string;
  sync?: {
    state?: string;
    error?: string;
    syncedAt?: number | null;
    pending?: number;
    members?: number;
  };
  /** The space's web page id (shared folders), for "Open on the web". */
  slug?: string;
  /** The owner's kept folders, empty ones too (a space's sync to everyone in it); the list adds the folders its rows are filed in. */
  folders?: string[];
  /** State `reset`: what this PC keeps of the space (plugin names, then "Workflows"), all of
   * which Upload my copy / Clear my copy act on. */
  resetContents?: string[];
}

/** A folder with everything in it, as one value (export_workflow_folder). Paths are
 *  relative to ``root``; a workflow's ``key`` is the id it had, which Run workflow
 *  steps inside the bundle use to name it. */
export interface WorkflowBundleDto {
  version: number;
  root: string;
  folders: string[];
  workflows: { key: string; name: string; description?: string; enabled?: boolean; folder: string; graph: AutomationGraphDto }[];
}

/** What copying, moving or importing a folder made. ``outside``: workflows its Run
 *  workflow steps call that were not part of it. */
export interface WorkflowFolderCopyDto {
  ok?: boolean;
  error?: string;
  folder?: string;
  owner?: string;
  moved?: number;
  outside?: string[];
  workflows?: { key: string; id: string; name: string }[];
}

export interface WorkflowOwnersDto {
  ok?: boolean;
  error?: string;
  owners?: WorkflowOwnerDto[];
  signedIn?: boolean;
  spacesEnabled?: boolean;
  /** How shared owners are named (the provider's word for it); "space" when unset. */
  kindLabel?: string;
  /** Workflows made while signed out on this PC, offered to the signed-in account. */
  localImport?: number;
}

/** What starts a workflow, for the list badge. */
export interface WorkflowTriggerDto {
  kind: "schedule" | "event" | "function" | "chat" | "manual";
  label: string;
}

export interface AutomationDto {
  id: string;
  name: string;
  enabled: boolean;
  description?: string;
  /** Folder path inside its owner ("Play tests/Tycoon"); "" = top level. */
  folder?: string;
  graph: AutomationGraphDto;
  runs?: AutomationRunDto[];
  updated?: number;
  last_run?: number;
  owner?: WorkflowOwnerDto;
  /** Shared workflows: this PC runs its schedule and triggers. */
  run_here?: boolean;
}

export interface AutomationSummaryDto {
  id: string;
  name: string;
  description?: string;
  enabled: boolean;
  updated?: number;
  last_run?: number;
  node_count?: number;
  owner?: WorkflowOwnerDto;
  run_here?: boolean;
  trigger?: WorkflowTriggerDto;
  folder?: string;
  /** Reusable workflows: what a Run workflow node passes in and gets back. */
  signature?: WorkflowSignatureDto | null;
}

export interface WorkflowSignatureDto {
  inputs: { name: string; default?: string; type?: PinType }[];
  outputs: string[];
  /** Pin types of the return values, when set. */
  output_types?: Record<string, PinType>;
}

export interface AutomationFieldDto {
  id: string;
  label?: string;
  type?: string;
  provider?: string;
  options?: Array<{ id: string; label?: string }>;
  /** file / files fields: what can be picked (image, audio, video, mesh, pdf, svg, any). */
  accept?: string;
  /** Custom code settings: the value used while none is set. */
  default?: unknown;
  /** backend fields: whose backends it picks from (a plugin's picture step lists Text to Image's). */
  node_type?: string;
}

export interface AutomationTemplateDto {
  id: string;
  name: string;
  label?: string;
  description?: string;
  icon?: string;
  kind?: string;
  plugin_id?: string;
  requires_plugins?: string[];
  missing_plugins?: string[];
  ready?: boolean;
  /** Its shelf in the picker: Images, 3D, Characters, Text & AI, Documents, Play tests, UEFN, Yours… */
  category?: string;
  /** "bundle": a folder template, several workflows in nested folders (``bundle``); its ``graph`` is empty. */
  shape?: "workflow" | "bundle";
  workflow_count?: number;
  folder_count?: number;
  /** A folder template's root folder name. */
  root?: string;
  bundle?: WorkflowBundleDto;
  graph: AutomationGraphDto;
}

/** What New workflow made from a template (use_workflow_template): one workflow, or a
 *  folder template's tree (``main`` is its first top-level workflow). */
export interface WorkflowTemplateUseDto extends WorkflowFolderCopyDto {
  shape?: "workflow" | "bundle";
  template?: string;
  main?: string;
  workflow?: AutomationDto;
}

export interface AutomationNodeDto {
  icon?: string;
  type: string;
  label: string;
  group: string;
  role?: string;
  description?: string;
  plugin_id?: string;
  config_fields?: AutomationFieldDto[];
  /** false: a data node (no white pins), run when one of its values is needed. */
  exec?: boolean;
  inputs?: PinDto[];
  outputs?: PinDto[];
  /** Image / 3D nodes: the backends its details offer. */
  backends?: AutomationBackendDto[];
  /** Spends credits on a plugin (needs its Spend credits switch on). */
  paid?: boolean;
}

/** Discord editor tab id — one tab per bot (`discord:<botId>`). Legacy `discord:main` = default. */
export function discordTabId(botId?: string): string {
  const bid = (botId || "default").trim() || "default";
  if (bid === "default" || bid === "main") return "discord:main";
  return `discord:${bid}`;
}

export function discordBotIdFromTab(tabId: string): string {
  if (!tabId.startsWith("discord:")) return "default";
  const rest = tabId.slice("discord:".length).trim();
  if (!rest || rest === "main") return "default";
  return rest;
}

/** Library ducky profile popped out as an editor tab (`ducky-profile:<profileId>`). */
export function duckyProfileTabId(profileId: string): string {
  return `ducky-profile:${profileId.trim()}`;
}

export function duckyProfileIdFromTab(tabId: string): string {
  return tabId.startsWith("ducky-profile:") ? tabId.slice("ducky-profile:".length) : "";
}

/** Plugin webview tab id — re-exported from plugin-ui for callers that already import panel helpers. */
export { pluginUiTabId, parsePluginUiTabId } from "../plugin-ui/types";

export type PlanTodoStatus = "pending" | "in_progress" | "completed" | "cancelled";

export type PlanNodeKind = "step" | "subplan";

export interface PlanNode {
  assignee?: string;
  /** Shown only: the group a lead's unassigned step names ("Edit & Render group implements…"). */
  assignee_inferred?: string;
  id: string;
  content: string;
  status: PlanTodoStatus;
  /** step = leaf todo; subplan = section that may nest children + body_markdown */
  kind?: PlanNodeKind;
  /** Associated details for this node (especially subplans). */
  body_markdown?: string;
  children?: PlanNode[];
}

/** Flat compat mirror of outline nodes (legacy UI paths). */
export interface PlanTodo {
  id: string;
  content: string;
  status: PlanTodoStatus;
}

export interface ChatPlan {
  id: string;
  chat_id: string;
  kind?: "project" | "template";
  title: string;
  overview?: string;
  body_markdown?: string;
  nodes?: PlanNode[];
  todos: PlanTodo[];
  template_id?: string | null;
  created_at?: number;
  updated_at?: number;
  status?: string;
  /** Set when this chat has no plan of its own and shows its group's part of a team plan (read-only). */
  assigned_from?: {
    chat_id: string;
    node_id: string;
    plan_title: string;
    assignee: string;
  };
  /** A member's own plan: the team's main plan it is part of, and the step its group works on. */
  team_plan?: {
    chat_id: string;
    title: string;
    node_id?: string;
  };
}

export interface PlanProgress {
  total: number;
  completed: number;
  cancelled: number;
  in_progress: number;
  pending: number;
}

export interface PlanOutlineRow {
  n: string;
  id: string;
  content: string;
  status?: PlanTodoStatus | string;
}

/** Summary row from Settings → Plans → Project Plans. */
export interface PlanListItem {
  chat_id: string;
  plan_id: string;
  kind?: "project";
  title: string;
  overview?: string;
  progress: PlanProgress;
  updated_at: number;
  created_at?: number;
  status?: string;
  template_id?: string | null;
  nodes?: PlanNode[];
  project_root: string;
  project_name: string;
  chat_title?: string;
}

/** Summary row from Settings → Plans → Templates. */
export interface PlanTemplateListItem {
  template_id: string;
  plan_id: string;
  kind: "template";
  title: string;
  overview?: string;
  updated_at: number;
  created_at?: number;
  status?: string;
  nodes?: PlanNode[];
  node_count?: number;
}

/** One line of a project's memory index (never carries the body). */
export interface MemoryEntryMeta {
  name: string;
  description: string;
  author: string;
  updated: string;
  chars: number;
  /** Sub-entries when the topic split like a skill (name is "entry/sub"). */
  subs?: Array<{ name: string; description: string }>;
}

/** A pulled memory entry: meta plus its full markdown body. */
export interface MemoryEntry extends Omit<MemoryEntryMeta, "chars"> {
  content: string;
  path: string;
}

export type SplitAxis = "row" | "column";

export interface EditorGroup {
  id: string;
  tabIds: string[];
  activeTabId: string | null;
  locked?: boolean;
}

export type EditorLayoutNode =
  | { type: "group"; groupId: string }
  | { type: "split"; id: string; axis: SplitAxis; children: EditorLayoutNode[]; sizes: number[] };

export interface EditorLayoutState {
  root: EditorLayoutNode;
  groups: Record<string, EditorGroup>;
  focusedGroupId: string;
}

export type EditorDropZone = "left" | "right" | "top" | "bottom" | "center";

export interface FocusWindowSnapshot {
  birthTabId: string;
  tabIds: string[];
  titles?: Record<string, string>;
  layout?: EditorLayoutState;
}

export interface EditorWorkspaceSnapshot {
  version: number;
  openTabs: EditorTab[];
  layout: EditorLayoutState;
  focusWindows?: FocusWindowSnapshot[];
}

export interface FolderItem {
  id: string;
  name: string;
  parentId: string;
  sortOrder: number;
  expanded: boolean;
  chats: ChatTab[];
  children: FolderItem[];
  /** When set, clicking the folder opens this group hub chat. */
  groupHubId?: string;
  /** The group hub is muted: the folder is one progress row that never expands. */
  muted?: boolean;
  /** Home project slug for synthetic all-projects wraps and their children. */
  projectSlug?: string;
}

export interface FolderDto {
  id: string;
  name: string;
  parent_id: string;
  sort_order: number;
  group_hub_id?: string;
  project_slug?: string;
  project_name?: string;
}

export interface SidebarChatLayout {
  id: string;
  folder_id: string;
  sort_order: number;
}

export interface SidebarLayoutPatch {
  folders: Array<{ id: string; parent_id: string; sort_order: number }>;
  chats: SidebarChatLayout[];
  /** Which project this layout belongs to (Global Agents is "_no_project"). Empty: the open one. */
  project_slug?: string;
}

export interface ToolCallData {
  id?: string;
  startedAt?: number;
  exitCode?: number;
  name: string;
  arguments: Record<string, unknown>;
  status?: "pending" | "success" | "error" | "cancelled" | string;
  durationMs?: number;
  result?: string;
  /** `load_messages` cut `result` (the card asks `load_tool_result` for the rest). */
  resultTruncated?: boolean;
  /** The whole result's size in bytes, when it was cut. */
  resultBytes?: number;
  llmTokens?: number;
  hint?: string;
  fileEdit?: FileEditData;
  /** Every changed file, grouped under the tool that changed it. */
  fileEdits?: FileEditData[];
}

export interface FileEditData {
  path: string;
  before: string;
  after: string;
  linesAdded: number;
  linesRemoved: number;
  kind: "write" | "create";
}

export interface ChatMessage {
  run_id?: string;
  checkpoint?: boolean;
  live_run?: boolean;
  id: number | string;
  role: "user" | "assistant" | "tool" | "success" | "error";
  text: string;
  tool?: ToolCallData;
  attachments?: MessageAttachmentDto[];
  thinking?: string;
  /** True when the turn crashed/stalled mid-stream and this is the kept partial. */
  incomplete?: boolean;
  /** Error text for an interrupted message (shown as a footer on the bubble). */
  error?: string;
  /** Group-chat speaker metadata (name/color/voice). */
  author?: MessageAuthorDto;
  /** Walkthrough ids this message offers as Start buttons (the welcome ducky). */
  tours?: string[];
  /** A user message sent into a running turn (Send now): it sits inside that turn, not before it. */
  steered?: boolean;
}

export interface VideoFrameDto {
  path?: string;
  t_s: number;
  abs_path?: string;
  media_url?: string;
}

export interface MessageAttachmentDto {
  /** audio / motion (an HTML scene whose render(t) draws frame t) come from generated media. */
  kind: "image" | "file" | "video" | "audio" | "motion";
  name: string;
  mime?: string;
  data_base64?: string;
  /** Generated media: the file name in Ducky's generated folder, and its absolute path. */
  generated?: string;
  path?: string;
  text?: string;
  /** Project Saved/DuckyCaptures path when the snip was mirrored there. */
  project_path?: string;
  /** Video: composer upload id (staging dir). */
  staged_id?: string;
  /** Video: bytes on disk. */
  size_bytes?: number;
  /** Video: absolute file once persisted in a chat (resend). */
  abs_path?: string;
  /** Video: local playback URL (/chat-attachments/...). */
  media_url?: string;
  /** Video: frames the AI saw (non-Gemini recipients). */
  frames?: VideoFrameDto[];
  /** File: how the model got it — pasted "whole", or saved for it to read in "parts". */
  sent?: "whole" | "parts";
}

export interface VideoPrepStatusDto {
  state: "queued" | "preparing_ffmpeg" | "extracting" | "transcribing" | "ready" | "error";
  frames_done: number;
  frames_total: number;
  transcript: "ok" | "none" | "skipped";
  transcript_note: string;
  error: string;
  /** Frames are ready, so the message can be sent even while the transcript is still running. */
  sendable?: boolean;
}

export type VideoAttachmentStatus = "uploading" | "preparing" | "ready" | "error";

export type ComposerAttachment =
  | { id: string; kind: "image"; name: string; mime: string; dataUrl: string; projectPath?: string }
  | { id: string; kind: "file"; name: string; mime: string; text: string }
  | {
      id: string;
      kind: "video";
      name: string;
      mime: string;
      sizeBytes: number;
      status: VideoAttachmentStatus;
      progress?: number;
      error?: string;
      stagedId?: string;
      absPath?: string;
      previewUrl?: string;
      /** Background frames/transcript preparation (set once staged). */
      prep?: VideoPrepStatusDto;
    };

export interface FfmpegStatusDto {
  state: "missing" | "installing" | "ready" | "error";
  progress: number;
  error: string;
  version: string;
  bundled?: boolean;
}

export interface VideoSettingsDto {
  ok?: boolean;
  video_max_mb: number;
  video_frames_per_video: number;
  max_images_per_message: number;
  /** Values 0 (= Auto) resolves to for a provider-agnostic caller. */
  auto?: {
    frames_per_video: number;
    max_images_per_message: number;
    /** The picked model's context window as its API reports it; 0 when unknown. */
    context_tokens?: number;
    /** Tokens of attached file text one message may carry (0 = no cap; the API decides). */
    file_max_tokens?: number;
    /** A file over this many tokens is saved and read in parts by the agent (0 = always pasted whole). */
    file_inline_tokens?: number;
  };
  ffmpeg: FfmpegStatusDto;
}

export interface ListenerStatus {
  online: boolean;
  wedged?: boolean;
  busy?: boolean;
  version: string;
  uptime_sec?: number;
  status_text?: string;
  uefn_project_dir?: string;
  uefn_project_name?: string;
  project_match?: boolean;
  /** The selected island's content mount, the other side of project_match. */
  selected_project_mount?: string;
  port?: number;
  epic_mcp_online?: boolean;
  epic_mcp_reason?: string;
  epic_mcp_url?: string;
  /** Editor holding Epic's MCP port: "uefn", "unreal" (Unreal Editor 5.8+), or "" unknown. */
  epic_mcp_editor?: string;
  epic_mcp_setup_steps?: string[];
  /** Set when Unreal Editor sits on UEFN's port: Ducky moved it to 8001, restart Unreal once. */
  epic_mcp_note?: string;
  uefn_python_editor_scripting?: boolean;
  uefn_mcp_toolsets?: boolean;
  python_and_toolsets?: boolean;
  listener_init_race?: boolean;
  beta_access_note?: string;
  plugin_connections?: PluginConnectionRow[];
  /** The header Online tab: who each plugin knows is around (plugin ``api.people``). */
  plugin_people?: PluginPeopleSection[];
}

export interface PluginPeopleSection {
  id: string;
  label: string;
  people: Array<{ id: string; name: string; detail: string; online: boolean }>;
}

export interface PluginConnectionRow {
  id: string;
  program: string;
  label: string;
  online: boolean;
  warn?: boolean;
  detail: string;
}

/** "uefn": an island (<Name>.uefnproject). "folder": any other folder, e.g. a code repo. */
export type ProjectKind = "uefn" | "folder";

/** What a project is, for its icon only: an island, or a folder that is a Roblox, Unreal,
 * Unity or Godot project, or just a folder. */
export type ProjectFlavor = "uefn" | "roblox" | "unreal" | "unity" | "godot" | "folder";

export interface ProjectInfo {
  path: string;
  name: string;
  slug: string;
  kind?: ProjectKind;
  flavor?: ProjectFlavor;
  /** Content pane root: "Content" on an island, "." for a folder project. */
  content_root?: string;
  /** The island's content mount (its root plugin name) — what UEFN reports as the live
   * project. Usually the folder name, but UEFN names some root plugins by a GUID. Empty
   * for a folder project. */
  mount?: string;
}

/** A row in the Projects window: a project Ducky opened, an island found on disk, or a
 * project a plugin lists. */
export interface KnownProject extends ProjectInfo {
  /** Its tab: "uefn", "other", or a plugin source id. */
  source: string;
  /** Unix seconds; 0 when never opened in Ducky. */
  last_opened: number;
  has_thumbnail?: boolean;
  thumbnail_url?: string;
  active: boolean;
  /** Duckies (chats) in this project. */
  duckies?: number;
  /** An island that already has Ducky's listener set up. */
  listener_ready?: boolean;
}

export interface SandboxProgram {
  pid: number;
  name: string;
  started: number;
  ports?: Record<string, number>;
  alive?: boolean;
  command?: string[];
  memory_mb?: number;
  cpu_percent?: number;
  listening?: number[];
}

export interface SandboxStatus {
  ok: boolean;
  exists?: boolean;
  path?: string;
  size_mb?: number;
  size_warning?: boolean;
  programs?: SandboxProgram[];
  pictures?: { path: string; media_url: string }[];
  error?: string;
}

/** An app a sandbox launched (header Apps menu); stays until its sandbox is deleted. */
export interface SandboxApp {
  conv_id: string;
  name: string;
  /** The chat that built it ('' when that chat is gone). */
  chat?: string;
  running: boolean;
  pid: number;
  ports: number[];
  launched: number;
  /** The last screenshot an agent took of it ('' when none). */
  picture: string;
}

export interface KnownProjectsListing {
  sources: { id: string; label: string; icon?: string }[];
  projects: KnownProject[];
}

export interface ProjectFolderInspection {
  ok: boolean;
  error?: string;
  path?: string;
  name?: string;
  kind?: ProjectKind;
  /** Islands one level down (someone picked the folder that holds their islands). */
  islands?: { name: string; path: string }[];
}

export interface ProjectFileEntry {
  name: string;
  path: string;
  is_dir: boolean;
  read_only?: boolean;
  /** content = current island; core = UEFN digests; project = another recent island. */
  kind?: "content" | "core" | "project";
}

export interface WorkspaceRootEntry {
  id: string;
  name: string;
  path: string;
  read_only: boolean;
}

export interface ProjectFileListing {
  path: string;
  entries: ProjectFileEntry[];
}

export interface ProjectFileStat {
  path: string;
  mtime_ns: number;
  size: number;
  exists: boolean;
}

export type SidebarPanelTab = "chats" | "files";

export type WorkspaceSearchScope = "files" | "chats" | "both";

export interface WorkspaceFileMatch {
  line: number;
  column: number;
  preview: string;
}

export interface WorkspaceFileSearchResult {
  path: string;
  matches: WorkspaceFileMatch[];
}

export interface WorkspaceChatMatch {
  message_id: number | string;
  preview: string;
}

export interface WorkspaceChatSearchResult {
  id: string;
  title: string;
  folder_id: string;
  ducky_style?: string;
  matches: WorkspaceChatMatch[];
}

export interface WorkspaceMemorySearchResult {
  name: string;
  preview: string;
}

export interface WorkspaceLedgerSearchResult {
  path: string;
  preview: string;
}

export interface WorkspaceHistorySearchResult {
  path: string;
  preview: string;
  ducky_name?: string;
}

export interface WorkspaceSearchResult {
  query: string;
  scope: WorkspaceSearchScope;
  file_results: WorkspaceFileSearchResult[];
  chat_results: WorkspaceChatSearchResult[];
  memory_results?: WorkspaceMemorySearchResult[];
  ledger_results?: WorkspaceLedgerSearchResult[];
  history_results?: WorkspaceHistorySearchResult[];
  stats: {
    file_count: number;
    file_match_count: number;
    chat_count: number;
    chat_match_count: number;
    memory_count?: number;
    ledger_count?: number;
    history_count?: number;
  };
}

export interface WorkspaceReplaceResult {
  query: string;
  replacement: string;
  scope: WorkspaceSearchScope;
  stats: {
    file_count: number;
    file_match_count: number;
    chat_count: number;
    chat_match_count: number;
  };
}

export interface RecentProject extends ProjectInfo {
  active: boolean;
}

export interface FileHistoryEntry {
  id: string;
  path: string;
  saved_at: number;
  bytes: number;
  preview?: string;
  content_hash?: string;
  /** "agent" | "user" | "revert"; empty/omitted for legacy user saves. */
  source?: string;
  /** 1 = pre-attribution entry; 2 = carries the writer fields below. */
  schema_version?: number;
  run_id?: string;
  conv_id?: string;
  profile_id?: string;
  ducky_name?: string;
  model?: string;
  tool?: string;
  group_id?: string;
  coding_agent?: string;
}

export interface ChangesetConflictDto {
  kind: "stale_base" | "concurrent_writer";
  other_run_id?: string;
  other_conv_id?: string;
  other_ducky?: string;
  expected_hash?: string;
  found_hash?: string;
}

/** What an editor command acted on. `guid` is durable; labels are neither unique nor stable. */
export interface EditorTargetDto {
  kind: "actor" | "asset" | "device" | "verse" | "material" | "datatable" | "niagara" | "umg" | "entity" | "world" | "other";
  id: string;
  guid?: string;
  label?: string;
  path?: string;
  invalid?: boolean;
}

/** One listener command to post when undoing an editor change. */
export interface EditorStepDto {
  command: string;
  params?: Record<string, unknown>;
}

/** What a ducky changed in the editor rather than on disk (schema changeset_run/1). */
export interface EditorChangeDto {
  command: string;
  kind?: string;
  facet?: string;
  targets?: EditorTargetDto[];
  /** Commands that put the target back. Empty means it cannot be undone automatically. */
  inverse?: EditorStepDto[];
  /** What this command brought into existence, so a revert can remove exactly that. */
  created?: EditorTargetDto[];
  revertable: "auto" | "manual" | "none";
  reason?: string;
  summary?: string;
  params?: Record<string, unknown>;
  /** `uefn`, `blender`, … — defaults to uefn when absent. */
  program?: string;
}

export interface ChangesetEntryDto {
  seq: number;
  ts: number;
  /** A project-relative file path, or a `uefn://…` target slot for editor changes. */
  path: string;
  op: "create" | "write" | "rename" | "move" | "copy" | "import" | "restore" | "delete" | "editor";
  from_path?: string | null;
  trash_token?: string | null;
  tool: string;
  before_hash: string;
  after_hash: string;
  before_blob?: string | null;
  after_blob?: string | null;
  lines_added?: number;
  lines_removed?: number;
  /** true/false when a lane governed the writer; null when no lane applied. */
  in_lane: boolean | null;
  conflict?: ChangesetConflictDto | null;
  reverted?: boolean;
  reverted_by_run?: string | null;
  /** Absent means "ok". Blocked and failed entries changed nothing and never revert. */
  outcome?: "ok" | "blocked" | "failed";
  /** Why it was refused or how it failed. */
  reason?: string | null;
  editor?: EditorChangeDto | null;
}

/** One agent run's project writes — schema changeset_run/1. */
export interface ChangesetRunDto {
  schema_version: number;
  run_id: string;
  conv_id: string;
  profile_id?: string;
  ducky_name?: string;
  model?: string;
  coding_agent?: string;
  group_id?: string;
  leader_conv_id?: string;
  source?: "agent" | "user" | "revert";
  /** The run this revert undid, when source is revert. */
  reverts_run_id?: string;
  archived?: boolean;
  lane?: { write_allowed: string[] } | null;
  started: number;
  ended?: number | null;
  status: "running" | "done" | "error" | "cancelled" | "reverted" | "partially_reverted";
  entries: ChangesetEntryDto[];
}

/** An editor change with no computable inverse: the user has to undo it by hand. */
export interface ChangesetManualRow {
  seq: number;
  /** The uefn:// target slot, or a file Ducky may not write in this project. */
  path: string;
  /** The target id or asset path, for the copy-out list. */
  target: string;
  label: string;
  command: string;
  summary: string;
  reason: string;
}

export interface ChangesetBlockedBy {
  other_conv_id: string;
  other_ducky: string;
  other_run_id: string;
  other_seq: number;
  path: string;
  /** Later write is in this same run — walk newest → oldest. */
  same_run?: boolean;
}

export interface ChangesetRevertResult {
  ok: boolean;
  run_id: string;
  revert_run_id?: string;
  reverted: number[];
  skipped_modified: { seq: number; path: string }[];
  /** Not errors: changes that were recorded but cannot be undone automatically. */
  manual?: ChangesetManualRow[];
  /** Another chat wrote this slot later — revert theirs first. */
  blocked_by?: ChangesetBlockedBy[];
  errors: string[];
}

export interface LaneCheckResult {
  ok: boolean;
  errors: string[];
  warnings: string[];
  normalized: string[] | null;
}

/** Every member's write lane for a group — schema lane_set/1. */
export interface LaneSetDto {
  schema_version: number;
  group_id: string;
  lanes: Record<string, { write_allowed: string[] | null; name?: string; ducky_name?: string; set_by?: string; set_at?: number }>;
}

export interface AppearanceSoundsDto {
  enabled?: boolean;
  volume?: number;
  /** hookId → soundRef (builtin:… | plugin:… | file:…). */
  mapping?: Record<string, string>;
}

export interface AppearanceDto {
  foundation: Record<string, string>;
  overrides: Record<string, string>;
  status_overrides: Record<string, Partial<Record<"bg" | "border" | "text" | "dim", string>>>;
  profiles?: AppearanceProfileDto[];
  active_profile_id?: string;
  /** Empty = none; ``matrix`` = built-in; ``plugin:<pluginId>:<effectId>`` from plugins. */
  effect_id?: string;
  /** When false, keep effect_id but do not mount the background animation. */
  effects_enabled?: boolean;
  /** Empty = none; ``plugin:<pluginId>:<skinId>`` full chrome skin. */
  skin_id?: string;
  /** Per plugin-profile customizations keyed by ``plugin:<pluginId>:<localId>``. */
  profile_patches?: Record<string, AppearanceProfilePatchDto>;
  /** Global sound-effect settings (not per-profile). */
  sounds?: AppearanceSoundsDto;
}

export interface AppearanceProfilePatchDto {
  foundation?: Record<string, string>;
  overrides?: Record<string, string>;
  status_overrides?: Record<string, Partial<Record<"bg" | "border" | "text" | "dim", string>>>;
  effect_id?: string;
  effects_enabled?: boolean;
  skin_id?: string;
}

export interface AppearanceProfileDto {
  id: string;
  name: string;
  foundation: Record<string, string>;
  overrides: Record<string, string>;
  status_overrides: Record<string, Partial<Record<"bg" | "border" | "text" | "dim", string>>>;
}

export interface PanelSettingsDto {
  agent_provider: string;
  agent_model: string;
  /** Qualified "backend:model_id" used when a Ducky/chat has no model of its own. */
  default_model?: string;
  /** Providers whose API key agents may start chats on (billed per token). */
  agent_api_keys?: string[];
  uefn_project_root: string;
  antigravity_config_path: string;
  verse_diagnostics_cache_enabled?: boolean;
  verse_diagnostics_auto_check?: boolean;
  ai_ignore_patterns?: string[];
  ai_ignore_strict?: boolean;
  allow_settings_write?: boolean;
  allow_agent_clicks?: boolean;
  allow_see_uefn?: boolean;
  allow_see_other_programs?: boolean;
  show_hidden_project_files?: boolean;
  terminals_enabled?: boolean;
  usage_stats?: boolean;
  prompt_caching_enabled?: boolean;
  freeze_prompt_prefix?: boolean;
  anthropic_extended_cache_ttl?: boolean;
  default_coding_agent?: string;
  duckyos_base_url?: string;
  remote_access?: boolean;
  voice_enabled?: boolean;
  voice_spoken_style?: string;
  voice_summary_model?: string;
  voice_default_voice?: string;
  voice_default_speed?: number;
  voice_live_auto_send?: boolean;
  /** 0–1: how much live voice narrates tools/thinking. */
  voice_process_talk?: number;
  /** empty = system/browser speech; openai = Whisper/Realtime. */
  voice_stt_provider?: string;
  mic_permission?: "ask" | "allow" | "block" | string;
  mic_device_id?: string;
  output_device_id?: string;
  tts_volume?: number;
  audio_muted?: boolean;
  memory_auto_compress?: boolean;
  prompt_dedupe_exact_blocks?: boolean;
  memory_keep_last_messages?: number;
  memory_compress_messages?: number;
  memory_compress_tokens?: number;
  memory_index_max_chars?: number;
  memory_summary_model?: string;
  /** Rename a new ducky after the role its first message asks for. */
  chat_auto_title?: boolean;
  /** Composer @ menu. Missing key stays on. */
  chat_mentions_enabled?: boolean;
  /** Composer / reference rows under the command list. Missing key stays on. */
  chat_slash_references_enabled?: boolean;
  /** Cheap model that refines the auto role title (empty = keyword names only). */
  chat_title_model?: string;
  /** Plugin id that makes pictures / 3D models when nothing else is picked (empty = first one set up). */
  default_image_provider?: string;
  default_mesh_provider?: string;
  /** Public web lookup: off, ask once per chat, or on. */
  web_access?: "off" | "ask" | "on" | string;
  /** Follow agent file edits in the editor (walkthrough). */
  follow_code_enabled?: boolean;
  follow_code_speed?: "slow" | "normal" | "fast" | "instant" | string;
  follow_code_split_beside_chat?: boolean;
  /** Product walkthrough completion flags keyed by tour id. */
  walkthrough_completed?: Record<string, boolean>;
  /** Start a view's tour the first time it opens (Workflows…). Missing key stays on. */
  first_open_tours?: boolean;
  /** Ducky's Show me plays right away; off = only the chat button. Missing key stays on. */
  show_me_autoplay?: boolean;
  coding_agents?: Record<
    string,
    {
      enabled?: boolean;
      cli_path?: string;
      default_args?: string;
    }
  >;
}

export interface MemorySettingsDto {
  ok?: boolean;
  memory_auto_compress: boolean;
  prompt_dedupe_exact_blocks: boolean;
  memory_keep_last_messages: number;
  memory_compress_messages: number;
  memory_compress_tokens: number;
  memory_index_max_chars: number;
  memory_summary_model: string;
  index_est_tokens?: number;
  index_chars?: number;
}

export interface ChatContextMemoryDto {
  ok?: boolean;
  error?: string;
  conv_id?: string;
  title?: string;
  ducky_name?: string;
  message_count?: number;
  keep_last?: number;
  context_summary?: string;
  context_summary_through?: number;
  context_summary_tokens?: number;
  estimated_history_tokens?: number;
  compress_recommended?: boolean;
  auto_compress?: boolean;
  compressed?: boolean;
  cleared?: boolean;
  method?: string;
  summary_preview?: string;
  reason?: string;
}

/** Another of your UEFN Duckies, and its chats as this Ducky last copied them. */
export interface DuckySyncPeer {
  ducky: string;
  name: string;
  synced: number;
  chats: Array<{ conv_id: string; title: string; updated: number; synced: number }>;
}

export interface DuckyOSAccountStatus {
  ok?: boolean;
  logged_in?: boolean;
  /** Host account key ("" signed out): keys per-account caches such as plugin prefs. */
  account?: string;
  needs_code?: boolean;
  browser_pending?: boolean;
  user_code?: string;
  plugin_disabled?: boolean;
  base_url?: string;
  default_base_url?: string;
  email?: string;
  display_name?: string;
  user_id?: string;
  roles?: string[];
  permissions?: string[];
  agent_denied?: string[];
  agent_settings?: AgentCapsSettings;
  device_key_active?: boolean;
  device_key_error?: string;
  session_expired?: boolean;
  error?: string;
  code?: string;
  email_hint?: string;
  ttl_seconds?: number;
}

export type AgentCapsSettings = {
  allow_settings_write?: boolean;
  allow_agent_clicks?: boolean;
  allow_see_uefn?: boolean;
  allow_see_other_programs?: boolean;
};

export type RemoteAccessStatus = {
  ok?: boolean;
  enabled?: boolean;
  running?: boolean;
  error?: string;
  site_update_pending?: boolean;
  sessions?: number;
  session_list?: { n: number; expires_in_s: number }[];
};

export type DuckyOSStoreItemState = "available" | "installed" | "update" | "unsupported";

export interface DuckyOSStoreItemDto {
  slug?: string;
  /** Install package type (plugin zip vs skill pack) — not a browse filter. */
  kind?: string;
  /** Legacy install bucket: plugins | skills */
  category?: string;
  /** Browse categories (multi): plugins, skills, themes, … */
  categories?: string[];
  tags?: string[];
  name?: string;
  description?: string;
  /** Optional public repository URL from the Store catalog. */
  repo_url?: string;
  latest_version?: string;
  pack_version?: number;
  /** Display version on disk ("1.0.11" or 37) — never compare, rank with pack_version. */
  installed_version?: number | string | null;
  state?: DuckyOSStoreItemState | string;
  enabled?: boolean | null;
  source?: string | null;
  install_count?: number | null;
  has_icon?: boolean;
  icon_mime?: string | null;
  icon_data_url?: string | null;
  /** Private items are listed only to their audience. */
  visibility?: "public" | "private";
  audience_id?: string | null;
  audience_label?: string;
  /** Shared with the signed-in account (Shared category). */
  mine?: boolean;
  /** Content summary when a plugin bundles skills/themes/etc. */
  contributes_summary?: string[];
}

export interface DuckyOSStoreAudienceNote {
  id: string;
  label: string;
  note: string;
}

export interface DuckyOSStoreCatalog {
  ok?: boolean;
  error?: string;
  code?: string;
  /** Account this catalog was fetched as ("" signed out) — caches are keyed by it. */
  account?: string;
  /** The account has private audiences (the Shared category shows); missing = false. */
  audiences?: boolean;
  /** Notes per audience (only with `audiences`), e.g. why its private items are hidden. */
  audienceNotes?: DuckyOSStoreAudienceNote[];
  /** What the Shared category is called (the provider's word for it). */
  audienceCategoryLabel?: string;
  /** What the empty Shared category says, and where its link goes. */
  audienceEmpty?: { text?: string; actionLabel?: string; path?: string } | null;
  items?: DuckyOSStoreItemDto[];
}

export interface DuckyOSStorePatchNote {
  version?: string;
  changelog?: string;
  created_at?: string | null;
  /** App versions only: the one-line changes the What's new window lists. */
  items?: WhatsNewItem[];
}

export type WhatsNewKind = "new" | "improved" | "fix";

export interface WhatsNewItem {
  kind: WhatsNewKind;
  text: string;
}

export interface WhatsNewVersion {
  version: string;
  created_at?: string | null;
  items: WhatsNewItem[];
}

export interface WhatsNewStatus {
  /** The running version. */
  current: string;
  last_seen?: string | null;
  /** When the Store released the running version. */
  released_at?: string | null;
  /** Open the window now with `versions`. */
  show: boolean;
  /** The versions not seen yet, newest first. */
  versions: WhatsNewVersion[];
  /** The latest versions with changes, newest first (Settings reopens these). */
  recent: WhatsNewVersion[];
  error?: string | null;
}

export interface DuckyOSStoreVersions {
  ok?: boolean;
  error?: string;
  code?: string;
  slug?: string;
  versions?: DuckyOSStorePatchNote[];
}

export interface DuckyOSStoreInstallResult {
  ok?: boolean;
  error?: string;
  code?: string;
  slug?: string;
  kind?: string;
  version?: string;
  pack_version?: number;
  import?: { ok?: boolean; error?: string; pack_id?: string; id?: string };
}

export interface UefnPluginContributionsDto {
  ok?: boolean;
  error?: string;
  settings_tabs?: Array<{ id: string; label?: string; icon?: string; ui?: string; plugin_id?: string }>;
  settings_sections?: Array<{
    id: string;
    tab: string;
    title?: string;
    description?: string;
    order?: number;
    plugin_id?: string;
    properties: Array<{
      id: string;
      type?: string;
      default?: boolean | string | number;
      label?: string;
      description?: string;
      placeholder?: string;
      options?: Array<{ value: string; label?: string }>;
    }>;
  }>;
  dock_panels?: Array<{
    id: string;
    title?: string;
    defaultSide?: string;
    ui?: string;
    css?: string;
    plugin_id?: string;
  }>;
  editor_kinds?: Array<{ kind: string; title?: string; ui?: string; plugin_id?: string }>;
  header_buttons?: Array<{
    id: string;
    title?: string;
    icon?: string;
    action: string;
    order?: number;
    plugin_id?: string;
  }>;
  ui_panels?: Array<{
    id: string;
    title?: string;
    icon?: string;
    entry: string;
    plugin_id?: string;
    version?: number | string;
  }>;
  chat_references?: Array<{
    trigger: "@" | "/" | string;
    id: string;
    label: string;
    group?: string;
    description?: string;
    href: string;
    plugin_id?: string;
  }>;
  shell_boots?: Array<{ plugin_id: string; entry: string }>;
  appearance_profiles?: Array<{
    id: string;
    name: string;
    plugin_id: string;
    foundation?: Record<string, string>;
    overrides?: Record<string, string>;
    status_overrides?: Record<string, Partial<Record<"bg" | "border" | "text" | "dim", string>>>;
  }>;
  appearance_css?: Array<{ plugin_id: string; entry: string }>;
  appearance_effects?: Array<{
    id: string;
    label: string;
    plugin_id: string;
    entry: string;
  }>;
  appearance_skins?: Array<{
    id: string;
    label: string;
    plugin_id: string;
    entry: string;
    css?: string;
  }>;
  sounds?: Array<{ id: string; label?: string; file: string; plugin_id: string }>;
  hooks?: Array<{ id: string; label?: string; plugin_id: string }>;
  automations_nodes?: Array<{
    id: string;
    label?: string;
    group?: string;
    description?: string;
    plugin_id: string;
  }>;
  automations_triggers?: Array<{
    id: string;
    label?: string;
    group?: string;
    description?: string;
    plugin_id: string;
  }>;
  /** New-file Verse scaffolds from `contributes.verse.templates`. */
  verse_templates?: Array<{
    id: string;
    name: string;
    icon: string;
    description?: string;
    content: string;
    order?: number;
    file?: string;
    folder?: string;
    files?: Array<{ path: string; content: string }>;
    connects?: string[];
    plugin_id: string;
  }>;
  tts_voices?: Array<{ id: string; label?: string; plugin_id: string }>;
  /** Enabled plugin ids that expose dynamic (runtime-fetched) TTS voices. */
  tts_voice_plugins?: string[];
  /** Gateway providers for Settings → LLMs (e.g. OpenAI / Ollama Store plugins). */
  llm_providers?: Array<{
    id: string;
    label: string;
    kind: "secret" | "url";
    secret_key: string;
    default_url?: string;
    order?: number;
    plugin_id: string;
    icon_data_url?: string;
    chat?: boolean;
  }>;
  /** Gateway coding agents (e.g. Codex via OpenAI plugin). */
  llm_coding_agents?: Array<{
    id: string;
    label: string;
    order?: number;
    plugin_id: string;
  }>;
  agent_tools?: Record<string, { category?: string; tools?: string[] }>;
  enabled_ids?: string[];
}

export interface UefnPluginDto {
  id: string;
  label?: string;
  description?: string;
  version?: number | string;
  kind?: string;
  source?: string;
  enabled?: boolean;
  local_trusted?: boolean;
  path?: string;
  /** Raw plugin.json contributes (settings.tabs, llm.providers, walkthrough, …). */
  contributes?: Record<string, unknown>;
  default_enabled?: boolean;
}

/** Coding-agent id: host ``ducky`` plus any Store gateway contribution id. */
export type CodingAgentId = string;

/** One plugin of the first-run bundle: an AI gateway or a UEFN editor tool. */
/** One crash report kept on this PC; `text` is exactly what was (or would be) sent. */
export interface CrashReportDto {
  id: string;
  sent: boolean;
  at: number;
  message: string;
  text: string;
}

export interface CrashReportsState {
  reports: CrashReportDto[];
}

export interface StarterPluginDto {
  slug: string;
  label: string;
  group: "ai" | "uefn" | "create" | "extras";
  installed: boolean;
}

export interface CodingAgentDto {
  id: CodingAgentId | string;
  label: string;
  enabled: boolean;
  available: boolean;
  status: string;
  cli_path?: string;
  default_args?: string;
  /** Store gateway that registered this agent (openai → Codex, …). */
  plugin_id?: string;
  /** From gateway plugin register(install_help=…). */
  install_help?: string;
  /** From gateway plugin (thinking_env / shows_thinking_effort). */
  shows_thinking_effort?: boolean;
  /** CLI account state from the gateway's detect: true/false, or null when n/a. */
  logged_in?: boolean | null;
  /** Gateway registered a `login` hook → Settings can run the CLI login itself. */
  can_login?: boolean;
  /** Gateway registered a `logout` hook → Settings can sign the CLI out. */
  can_logout?: boolean;
  capabilities?: {
    terminal_agent?: boolean;
    chat_api?: boolean;
    a2a?: boolean;
    mcp_inject?: boolean;
    needs_api_key?: boolean;
    needs_cli?: boolean;
  };
  models?: Array<{
    id: string;
    name: string;
    provider?: string;
    context_limit?: number;
    price_in?: number | null;
    price_out?: number | null;
    supports_vision?: boolean;
    supports_tools?: boolean;
    supports_web_search?: boolean;
    supports_thinking_effort?: boolean | null;
    thinking_menu?: {
      lo?: string;
      hi?: string;
      levels: Array<{ id: string; label?: string; thinking_tokens?: number | null; hint?: string }>;
    } | null;
  }>;
}

export type LinkedAgentStatus = "running" | "done" | "error" | "timeout" | "cancelled";

export interface LinkedAgent {
  childConvId: string;
  title: string;
  status: LinkedAgentStatus;
}

export interface AgentEvent {
  type:
    | "agent_started"
    | "text_delta"
    | "thinking"
    | "tool"
    | "tool_done"
    | "assistant_done"
    | "agent_stopped"
    | "error"
    | "status"
    | "usage"
    | "linked_agent"
    | "chats_changed"
    | "context_changed"
    | "editor_batch"
    | "file_sync"
    | "verse_diagnostics_sync"
    | "verse_diagnostics_progress"
    | "verse_workflow_state"
    | "terminal_open"
    | "terminal_close"
    | "terminal_command_pending"
    | "terminal_command_decided"
    | "tab_claimed"
    | "tab_focus_request"
    | "file_renamed"
    | "file_deleted"
    | "delegation_warning"
    | "plan_updated"
    | "plan_assignment_changed"
    | "discord_message"
    | "ui_rpc_request"
    | "ui_rpc_settled"
    | "open_coding_agent_login"
    | "settings_changed"
    | "mcp_plugins_changed"
    | "skills_changed"
    | "duckies_changed"
    | "file_guard"
    | "lane_changed"
    | "files_reverted"
    | "window_rtc"
    | "direct_rtc"
    | "rtc_cmd"
    | "live_message"
    | "live_peers"
    | "open_url"
    | "model_needed"
    | "model_adopted"
    | "provider_not_connected"
    | "user_steered"
    | "run_expect"
    /** Phone panel only (local): the link to the PC is back after a drop; reload what was missed. */
    | "remote_resync";
  text?: string;
  /** run_expect: how long the chat's running sandbox/terminal command should take (ms, 0 = ended). */
  expected_ms?: number;
  /** run_expect: expected_ms is the median of this command's past runs, not the agent's own figure. */
  estimated?: boolean;
  /** user_steered: id of the queued prompt the running turn took in. */
  steer_id?: string;
  /** assistant_done: a group note (a member's work posted to its group), not the end of a turn. */
  note?: boolean;
  /** ui_rpc_request: which panel method to run and its params. */
  method?: string;
  params?: Record<string, unknown>;
  success?: boolean;
  conv_id?: string;
  run_id?: string;
  /** Already in the server backlog when this page loaded: history, not a live run. */
  replayed?: boolean;
  /** When this page received it (ms). Delivery can lag while the window is minimized. */
  received_at?: number;
  /** On an error event: true when the backend kept the partial reasoning/answer. */
  kept_partial?: boolean;
  reason?: "cancelled" | "done" | "error" | string;
  parent_conv_id?: string;
  child_conv_id?: string;
  title?: string;
  /** chats_changed: false keeps the sidebar in sync without stealing the open tab. */
  open?: boolean;
  /** chats_changed: chats deleted or archived (they leave every window's tree at once). */
  removed_conv_ids?: string[];
  /** chats_changed: folders and groups deleted. */
  removed_folder_ids?: string[];
  status?: LinkedAgentStatus;
  /** Group voice: which ducky just finished speaking this turn. */
  author?: MessageAuthorDto;
  folder_id?: string;
  input_tokens?: number;
  plan?: ChatPlan;
  progress?: PlanProgress;
  output_tokens?: number;
  total_tokens?: number;
  total_cache_read?: number;
  total_cache_write?: number;
  cache_hit_rate?: number;
  call_count?: number;
  call_input?: number;
  call_output?: number;
  call_cache_read?: number;
  call_cache_write?: number;
  step?: number;
  calls?: TokenUsageCall[];
  tool?: ToolCallData;
  editor_batch?: { actions: unknown[]; conv_id?: string };
  path?: string;
  /** file_guard: "lane_denied" | "shadow_violation" | "conflict" | "policy_denied". */
  kind?: string;
  lane?: string[];
  leader_conv_id?: string;
  /** files_reverted: project paths restored. */
  paths?: string[];
  /** lane_changed */
  group_id?: string;
  member_conv_id?: string;
  write_allowed?: string[] | null;
  /** file_deleted / file_renamed: relative path that disappeared (or the old name). */
  old_path?: string;
  /** file_renamed: relative path after the rename. */
  new_path?: string;
  files?: Array<{ path: string; errors: number; warnings: number; items?: unknown[] }>;
  project_root?: string;
  phase?: string;
  index?: number;
  total?: number;
  message?: string;
  file?: { path: string; errors: number; warnings: number; items?: unknown[] };
  connected?: boolean;
  build_state?: number;
  can_push_verse_changes?: boolean;
  last_log?: string;
  session_id?: string;
  shell?: string;
  ws_url?: string;
  cwd?: string;
  /** terminal_open: false parks in the header list without focusing the editor tab. */
  activate?: boolean;
  /** terminal_open: title of the ducky (chat) that opened it. */
  owner?: string;
  request_id?: string;
  command?: string;
  source?: string;
  /** terminal_command_pending: what "Always allow … in this chat" names ('' = not offered). */
  rule_label?: string;
  /** terminal_command_pending: unix seconds when the agent asked. */
  created_at?: number;
  channel_id?: string;
  /** open_coding_agent_login: Settings → LLMs → this provider's detail slide. */
  provider_id?: string;
  /** Multi-bot Discord: which bot profile emitted this event. */
  bot_id?: string;
  discord?: DiscordMessageDto;
  /** window_rtc: target HWND the remote viewer asked to watch. */
  hwnd?: number | string;
  payload?: Record<string, unknown>;
  /** direct_rtc: viewer session id, full-ICE offer, ICE servers to use. */
  session?: string;
  offer?: { type: string; sdp: string };
  ice?: RTCIceServer[];
  /** direct_rtc: unix seconds when the offer was published (stale-guard). */
  ts?: number;
  /** open_url: https link to open on the remote viewer. */
  url?: string;
  /** model_needed: messages this chat keeps until a model is picked. */
  held?: number;
  /** model_adopted: the model a chat with none just took (the Default Model or Ducky AI). */
  model?: string;
  coding_agent?: string;
  provider?: string;
  /** provider_not_connected: the gateway's name as Settings → LLMs shows it. */
  label?: string;
}

export interface WindowBox {
  left?: number;
  top?: number;
  right?: number;
  bottom?: number;
  screen_left?: number;
  screen_top?: number;
  screen_w?: number;
  screen_h?: number;
  primary_w?: number;
  primary_h?: number;
}

export interface DiscordMessageDto {
  id: string;
  author: string;
  author_id: string;
  bot: boolean;
  content: string;
  timestamp: string;
}

export interface DiscordChannelDto {
  id: string;
  name: string;
  position: number;
}

export interface DiscordBotDto {
  id: string;
  label: string;
  guild_id: string;
  post_as: string;
  allowed_ids: string;
  prefix: string;
  enabled: boolean;
  /** When true, Discord shows the bot offline; default false = always Online while responding. */
  show_offline?: boolean;
  channel_id?: string;
  has_token?: boolean;
  configured?: boolean;
}

export interface DiscordStatusDto {
  ok: boolean;
  configured?: boolean;
  guild_set?: boolean;
  bot_id?: string;
  bot_name?: string;
  guild_id?: string;
  label?: string;
  prefix?: string;
  enabled?: boolean;
  /** Optional posting identity — sends become "**name:** text"; blank = post as the bot. */
  post_name?: string;
  /** Comma-separated Discord user IDs allowed to run commands. Blank = locked; "*" = anyone. */
  allowed_ids?: string;
  error?: string;
  plugin_disabled?: boolean;
}

export type DiscordMemberStatus = "online" | "idle" | "dnd" | "offline";

export interface DiscordMemberDto {
  id: string;
  name: string;
  bot: boolean;
  status: DiscordMemberStatus;
  /** Discord role color as integer RGB, or null. */
  color?: number | null;
}

export interface DiscordMemberGroupDto {
  id: string;
  name: string;
  count: number;
  members: DiscordMemberDto[];
}

export interface BrowserPaneState {
  ok?: boolean;
  error?: string;
  pane_id?: string;
  url?: string;
  title?: string;
  can_back?: boolean;
  can_forward?: boolean;
  loading?: boolean;
  ready?: boolean;
  failed?: string;
  queued?: boolean;
}

export interface PanelPushEvent {
  type:
    | "key_test_done"
    | "key_test_progress"
    | "appearance_changed"
    | "project_changed"
    | "discord_changed"
    | "uefn_plugins_changed"
    | "duckyos_account_changed"
    | "plugin_scope_changed"
    /** A write of a plugin (an agent's tool call, its folder) was refused: someone holds the unit. */
    | "plugin_write_refused"
    /** A teammate (`holder`) asks this PC to release its lock on `unit`. */
    | "plugin_lock_asked"
    /** A lock this PC held went: `holder` took it (broke it), or its lease ran out (no holder). */
    | "plugin_lock_lost"
    | "models_updated"
    | "coding_agents_updated"
    | "uefn_plugin_trust_request"
    | "browser_pane_state"
    | "browser_pane_new_window"
    /** An agent asks a Browser tab's page to open / switch / close a page tab. */
    | "browser_tab_command"
    | "background_job"
    | "graphs_changed"
    | "graph_focus"
    | "workflow_run"
    | "workflow_step"
    | "workflow_output"
    | "templates_changed"
    | "starter_plugins_progress"
    | "plugin_hook"
    | "appearance_profile_requested"
    /** A Windows notification was clicked: show this chat or workflow (`kind` + `id`). */
    | "open_target"
    /** Live editing: a teammate's message for `plugin` (`from`, `topic`, `data`). */
    | "live_message"
    /** Live editing: who is live in `plugin` now (`on`, `peers`). */
    | "live_peers"
    /** What teammates hold in `plugin` now (`holds`), from their presence. */
    | "live_holds"
    /** A groupmate's live state in `plugin` (`from`, `state`; null: gone). */
    | "live_member_state"
    /** Who is live where on the team copy `space` moved, or this PC's Live there (`on`, `ended`). */
    | "live_team"
    /** One of the group's shared values in `plugin` changed (`key`, `value`, `by`). */
    | "live_shared"
    /** A teammate invites this PC into their live group (`account`, `space`, `room`, `from`, `expires`). */
    | "live_invite"
    /** Another PC pushed to the island UEFN has open (`island`, `name`, `behind`, `count`, `detail`); or it is up to date again. */
    | "island_updates";
  provider?: string;
  /** plugin_hook: the hook id; appearance_profile_requested: the plugin profile id. */
  id?: string;
  /** background_job: the chat a row opens (a workflow's Message me). */
  conv_id?: string;
  /** plugin_hook / appearance_profile_requested: when the plugin asked (epoch seconds). */
  at?: number;
  /** plugin_hook: what the plugin passed along with it. */
  payload?: Record<string, unknown>;
  phase?: "working" | "ready" | "done" | "error";
  percent?: number | null;
  cancelable?: boolean;
  ok?: boolean;
  detail?: string;
  appearance?: AppearanceDto;
  project?: ProjectInfo;
  /** uefn_plugin_trust_request — AI/local plugin awaiting user confirm. */
  plugin_id?: string;
  /** plugin_scope_changed — plugins whose data a sync changed (empty = scope switched). */
  plugins?: string[];
  /** plugin_scope_changed from a sync round (status refresh even when nothing changed). */
  synced?: boolean;
  /** plugin_scope_changed after a switch: the plugin restarted on the other copy; its panels reload. */
  switched?: boolean;
  /** plugin_scope_changed for the bar only (a lock, the mode, a switch starting): no data changed. */
  status?: boolean;
  /** plugin_scope_changed from a sync: per plugin, the doc keys and file paths its pull changed. */
  keys?: Record<string, string[]>;
  /** live_message / live_peers / live_holds: the plugin it is for. */
  plugin?: string;
  /** live_message: what it is about (`from` carries who sent it: name, user, device). */
  topic?: string;
  data?: unknown;
  /** live_peers: live in this plugin, and with whom. */
  on?: boolean;
  peers?: PluginLivePeer[];
  /** live_peers / live_team: Live ended by itself ("alone": everyone else left the group). */
  ended?: string;
  /** live_team / live_invite: the team copy. */
  space?: string;
  /** live_invite: the group asked into, and when the invite runs out (epoch seconds). */
  room?: string;
  expires?: number;
  /** live_holds: the controls teammates are pressing. */
  holds?: PluginLiveHold[];
  /** plugin_write_refused: the unit, its name, who holds it and since when (epoch seconds).
   * plugin_lock_asked: who asks. plugin_lock_lost: who has it now, and `kept` (unsent edits
   * kept as a conflict). */
  unit?: string;
  unit_name?: string;
  holder?: string;
  /** plugin_write_refused: the PC's name when this account holds it on another PC. */
  elsewhere?: string;
  since?: number;
  kept?: boolean;
  /** duckyos_account_changed — the new account key ("" signed out). */
  account?: string;
  source?: string;
  kind?: string;
  /** graph_focus: saved, show or deleted. */
  action?: string;
  /** graph_focus: the nodes to bring into view (an agent's change, or what it is showing). */
  nodes?: string[];
  /** graph_focus: select them too (opens their details). */
  select?: boolean;
  /** graph_focus: a short caption shown over the canvas. */
  note?: string;
  /** workflow_run / workflow_step: which run, the step (node id) and the step it came from. */
  run?: string;
  /** Per-run event order, including snapshots from another process. */
  workflow_sequence?: number;
  /** workflow_output: bounded terminal snapshot for this node. */
  output?: string;
  session_id?: string;
  node?: string;
  from?: string;
  /** workflow_run: started | done | error | stopped; workflow_step: running | ok | error | stopped. */
  state?: string;
  error?: string;
  /** workflow_run started: the chat that ran it, the workflow's name and its happy-path steps in order. */
  conv?: string;
  name?: string;
  plan?: { node: string; label: string; type?: string }[];
  /** browser_pane_state fields (native WebView2 pane navigation state). */
  pane_id?: string;
  url?: string;
  title?: string;
  can_back?: boolean;
  can_forward?: boolean;
  loading?: boolean;
  ready?: boolean;
  failed?: string;
  /** starter_plugins_progress — one slug in the popular bundle. */
  slug?: string;
  label?: string;
  index?: number;
  total?: number;
  setup_phase?: "installing" | "installed" | "skipped" | "error";
  /** island_updates: the island's folder (`name` its name), whether it lacks remote revisions and how many came from another PC. */
  island?: string;
  behind?: boolean;
  count?: number;
}

/** Whose plugin data a panel shows (host scope bar). */
export interface PluginScopeView {
  kind: "personal" | "shared" | "sandbox";
  label: string;
  spaceId: string;
  readOnly: boolean;
}

export interface PluginScopeUsage {
  usedBytes?: number;
  limitBytes?: number;
  docsBytes?: number;
  assetsBytes?: number;
  packagesBytes?: number;
}

/** A teammate live in the same room, and how this PC reaches them. */
export interface PluginLivePeer {
  name?: string;
  user?: string;
  device?: string;
  /** Their PC's name. */
  label?: string;
  /** `direct`: straight link; `forwarded`: through a teammate; `none`: not linked (changes come with sync). */
  path?: "direct" | "forwarded" | "none";
  /** forwarded: the teammate it goes through. */
  via?: string;
}

/** One live group of a plugin (backend/uefn_plugins/live.py `groups`): a room named after
 * the PC that started it. */
export interface PluginLiveGroup {
  room: string;
  /** The leader's name: the PC the room is named after while it is in it, else whoever has
   * been in the group longest; one on an older app leads only when nobody up to date is in it. */
  host: string;
  hostDevice?: string;
  hostIsMe?: boolean;
  /** The first to come first; this PC is `me`. `older`: on an older UEFN Ducky, whose screen
   * doesn't follow the group (the leader still does their tab clicks for everyone). */
  members: { name: string; user?: string; device?: string; label?: string; me?: boolean; older?: boolean }[];
  /** This PC is in it. */
  mine?: boolean;
}

/** A control a teammate is pressing (backend/uefn_plugins/live.py `roster_holds`). */
export interface PluginLiveHold {
  device: string;
  name: string;
  anchor: string;
  /** When it was pressed (ms). */
  t: number;
  /** The view it was pressed in. */
  v: string;
}

/** Live editing on a team copy (backend/uefn_plugins/live.py `status`). */
export interface PluginLiveStatus {
  supported?: boolean;
  /** Why not, when not supported. */
  reason?: string;
  on?: boolean;
  room?: string;
  state?: string;
  peers?: PluginLivePeer[];
  /** Names of people in this PC's group with no link to this PC yet (just joined), and
   * with none after that (pointers, follow and "pull now" never reach them). */
  connecting?: string[];
  unlinked?: string[];
  /** The plugin's live groups in the team, this PC's first. */
  groups?: PluginLiveGroup[];
  /** What this PC goes back into on the team copy while not live (after a restart): "start"
   * (Start live), a teammate's room still live, or "" (Leave, or everyone else left). */
  rejoin?: string;
}

/** One person in a live group of a team copy (backend/uefn_plugins/live.py `team_groups`). */
export interface LiveTeamMember {
  name: string;
  user?: string;
  device?: string;
  label?: string;
  me?: boolean;
  /** On an older UEFN Ducky whose screen doesn't follow the group. */
  older?: boolean;
  /** Live in one plugin only: an app from before one Live covered every plugin. */
  partial?: boolean;
  /** The plugin they have on screen, and its name ("" for none). */
  plugin?: string;
  pluginLabel?: string;
}

/** A teammate with the app open on the team copy, not in this PC's group (live.py `online`). */
export interface LiveTeamOnline {
  name: string;
  user?: string;
  device: string;
  /** Live in another group. */
  live?: boolean;
  /** An invite from this PC still stands (30 s). */
  invited?: boolean;
}

export interface LiveTeamGroup extends Omit<PluginLiveGroup, "members"> {
  members: LiveTeamMember[];
}

/** One team copy in the title bar's Live menu (live.py `overview`). */
export interface LiveTeam {
  account: string;
  space: string;
  label: string;
  /** A plugin here can go live on it; `reason` says why not. */
  supported: boolean;
  reason?: string;
  on: boolean;
  room?: string;
  state?: string;
  /** This PC's plugins in the group. */
  plugins?: string[];
  groups: LiveTeamGroup[];
  /** People in this PC's group with no link yet (just joined), and with none after that. */
  connecting?: string[];
  unlinked?: string[];
  rejoin?: string;
  online?: LiveTeamOnline[];
}

export interface LiveOverview {
  ok?: boolean;
  on?: boolean;
  teams?: LiveTeam[];
  error?: string;
}

export interface PluginScopeStatus {
  ok?: boolean;
  /** False while the account has no shared spaces to offer, or the plugin never shares
   * its data (`"data_sharing": false`): no bar at all. */
  visible?: boolean;
  /** False for a plugin that keeps its own files (nothing goes through the host data
   * service): no shared space is offered, and a visible bar says Local only. */
  shareable?: boolean;
  /** How shared spaces are named (the provider's word for it); "space" when unset. */
  kindLabel?: string;
  scope?: PluginScopeView;
  /** The plugin's display name (its id when the manifest has none). */
  pluginLabel?: string;
  email?: string;
  canChange?: boolean;
  members?: number;
  /** ok | paused | offline | error | unavailable | waiting | locked | lost (access to the
   * space lost) | reset (its data was reset on the server) | no_provider (nothing to sync
   * with; changes still queue) | binding (a switch is getting a team's folder files first,
   * see `binding`). `error` carries the reason. */
  state?: string;
  error?: string;
  syncedAt?: number | null;
  /** This plugin's changes still to send (refused and blocked ones aren't counted). */
  pending?: number;
  /** This plugin's changes that aren't going out, per state (shared copy only). */
  refused?: number;
  blocked?: number;
  conflict?: number;
  /** Those changes, newest first, each with its reason. */
  items?: PluginScopeItem[];
  /** State `reset`: what this PC keeps of the space (plugin names, then "Workflows"), all of
   * which Upload my copy / Clear my copy act on. */
  resetContents?: string[];
  usage?: PluginScopeUsage;
  /** This plugin's Local copy, and the copy it shows now (the same when Local). */
  local?: PluginDataTotals;
  current?: PluginDataTotals;
  /** The space has a web page, for "View on the web" (shared copy only). */
  webPage?: boolean;
  /** What a paused (read-only) shared copy says, and a link that helps. */
  pausedText?: string;
  pausedAction?: { label: string; path: string };
  /** Lost access: when (epoch seconds) the locked copy leaves this PC unless access comes back. */
  deleteAt?: number;
  /** Lost access: this PC's changes to the space never sent (Save my unsent changes to Local). */
  unsent?: number;
  /** Lost access: the person already chose what happens to them (saved or discarded). */
  unsentChosen?: boolean;
  /** Who else is in the space now (presence); never this PC. */
  people?: PluginScopePerson[];
  /** Live editing: the Go live button and who is live with you. */
  live?: PluginLiveStatus;
  /** Live locks on this plugin's units. */
  locks?: PluginScopeLock[];
  /** What goes out and comes in (team repo copies). */
  changes?: PluginScopeChanges;
  /** Items changed here and by someone else, kept until resolved. */
  conflicts?: PluginScopeConflict[];
  /** v1: the provider's older sync (no people, locks, history or mode); v2: the new sync. */
  engine?: "v1" | "v2";
  /** auto: rounds push and pull by themselves; manual: only Push and Pull do. */
  mode?: "auto" | "manual";
  /** The last changeset applied on this PC (#482). */
  head?: number;
  /** When (epoch seconds) a failed round tries again. */
  nextTry?: number | null;
  /** The plugin's data is still moving to the new sync: percent done. */
  importing?: number | null;
  /** A plugin's declared folders on a team copy: one row per unit. */
  folders?: PluginScopeFolderUnit[];
  /** A plugin that keeps its own folders (`team_folders` in plugin.json), in any copy: each
   * declared folder, and why it can't be shared with a team from this PC, if so. */
  teamFolders?: PluginScopeTeamFolder[];
  /** Teams this plugin was linked to before its folders could be shared: the bar offers to
   * copy its Local folders into them. */
  folderOffers?: { space: string; label: string }[];
  /** Folder files are stored as the plugin writes them on each PC: the first share and the
   * help say so. */
  folderNote?: string;
  /** State `binding`: a switch to a team is getting that team's folder files first
   * ("Getting Alpha's files… 40%"); the plugin still reads its current copy meanwhile. */
  binding?: { team?: string; pct?: number | null; at?: number; text?: string } | null;
}

/** One declared folder of a plugin. */
export interface PluginScopeTeamFolder {
  id: string;
  root?: string;
  /** A `{PROJECT}` folder: the open project's id ("" for other folders). */
  projectId?: string;
  /** Why this folder can't be shared with a team from this PC ("" when it can): a
   * revision-controlled island, OneDrive, a network or non-NTFS drive. */
  refused?: string;
  /** Moved into the Local copy (its old place is a link now). */
  adopted?: boolean;
  /** The space its link points at ("personal" for Local, "" when not linked). */
  bound?: string;
  afterPull?: string;
}

/** A unit of a plugin's Local folder copy that a first share can copy into a team. */
export interface PluginFolderShareUnit {
  folder?: string;
  unit: string;
  name: string;
  files?: number;
  bytes?: number;
}

/** A switch into a team whose folder copy is empty: which Local units to copy first, or
 * start with it empty. */
export interface PluginScopeFirstShare {
  units?: string[];
  start_empty?: boolean;
  /** Ask which Local units to copy even when the team already has files ("Copy Local into"). */
  pick?: boolean;
}

/** Someone in a shared space now. Either one row per activity (`doing`), or a roster row
 * with each plugin's activity in `plugins`. */
export interface PluginScopePerson {
  name?: string;
  user?: string;
  user_name?: string;
  /** The PC's label. */
  device?: string;
  /** The PC's device id (a live peer's `device`). */
  deviceId?: string;
  /** This PC: never listed. */
  me?: boolean;
  /** This account on another PC ("You on MainPC"): locked out here like anyone else. */
  self?: boolean;
  /** viewing | editing | "" (in the space, not in this plugin). */
  doing?: string;
  unitName?: string;
  /** user | agent (one of their duckies). */
  actor?: string;
  ducky?: string;
  /** Epoch seconds. */
  since?: number;
  plugins?: Record<string, {
    viewing?: boolean;
    editing?: { unitName?: string; actor?: string; ducky?: string; since?: number }[];
    /** The live room they are in for this plugin ("" when not live). */
    live?: string;
  }>;
}

export interface PluginScopeLock {
  unit: string;
  /** The unit's name as people know it. */
  name?: string;
  holder?: string;
  /** The PC's name when this account holds it on another PC (holder is "you on <PC>"). */
  elsewhere?: string;
  /** Held by this PC. */
  mine?: boolean;
  /** Epoch seconds. */
  since?: number;
  /** Seconds left on the lease (server clock). */
  leaseLeft?: number;
}

/** One change of this PC not sent yet. */
export interface PluginScopeOutgoing {
  kind: string;
  key: string;
  /** Shown instead of the key (a folder unit's name). */
  name?: string;
  size?: number;
  /** A folder unit (kind `tree`, key `<folder>/<unit>`): its folder, its unit and how many
   * of its files changed. */
  folder_id?: string;
  unit?: string;
  files?: number;
  deleted?: boolean;
  /** queued | sending | refused | blocked | locked_out | conflict; a folder unit also
   * quarantined (changed while someone else holds it) | refetch (the team's version is
   * being fetched again) */
  state: string;
  reason?: string;
  attempts?: number;
}

/** An incoming item that couldn't be applied here; it is retried alone. */
export interface PluginScopeIncomingBlocked {
  kind: string;
  key: string;
  name?: string;
  error?: string;
  attempts?: number;
}

export interface PluginScopeChanges {
  outgoing?: PluginScopeOutgoing[];
  /** Changesets on the server not applied here yet, and who made them. */
  incoming?: number;
  incomingBy?: string[];
  /** The incoming changes wait for Update now (a plugin that restarts to take them, with
   * its panel on screen). */
  held?: boolean;
  blocked?: PluginScopeIncomingBlocked[];
  /** An upload running now: bytes sent of the total. */
  sentBytes?: number;
  totalBytes?: number;
}

export interface PluginScopeConflict {
  kind: string;
  key: string;
  name?: string;
  unit?: string;
  /** A folder unit (kind `tree`, key `<folder>/<unit>`): its folder. */
  folder_id?: string;
  /** edit (both changed it) | deleted (they deleted, you changed) | mine_deleted | lock_broken */
  reason?: string;
  /** Whose version won here, and when (epoch seconds). */
  by?: string;
  at?: number;
  theirsSeq?: number;
}

/** A unit of a declared folder. */
export interface PluginScopeFolderUnit {
  /** The unit inside its folder (a `{PROJECT}` folder's units start with the project id). */
  unit: string;
  name: string;
  /** The declared folder's id. */
  folder_id?: string;
  /** The id its lock goes by (`tree:<folder>/<hash>`). */
  unitId?: string;
  /** up_to_date | changed_here | locked_by | incoming (the team's change waits behind
   * Update now) | conflict | quarantined (changed here while someone else holds it: never
   * sent) | refused (`reason` says why); also synced / ok, changed, locked. */
  state: string;
  holder?: string;
  /** The PC's name when this account holds it on another PC. */
  elsewhere?: string;
  /** Held by this PC. */
  mine?: boolean;
  files?: number;
  bytes?: number;
  reason?: string;
}

/** A changeset (`count` items), or one item's version (`kind`/`key` set). */
export interface PluginScopeHistoryEntry {
  seq: number;
  by?: string;
  at?: number;
  device?: string;
  actor?: string;
  ducky?: string;
  message?: string;
  count?: number;
  kind?: string;
  key?: string;
  deleted?: boolean;
  size?: number;
  /** A changeset's items as [kind, key]. */
  items?: [string, string][];
  /** Sent by this live group's leader for the groupmate in `by` (on the PC in `device`). */
  via?: string;
}

export interface PluginScopeResult {
  ok?: boolean;
  error?: string;
}

/** One of a plugin's changes that isn't going out to its shared space. */
export interface PluginScopeItem {
  /** doc | asset */
  kind: string;
  key: string;
  /** refused (by the server) | blocked (failed too often) | conflict | locked_out */
  state: string;
  reason?: string;
  attempts?: number;
  /** The change is a delete. */
  deleted?: boolean;
}

export interface PluginDataTotals {
  docs: number;
  files: number;
  docsBytes: number;
  filesBytes: number;
}

export interface PluginScopeChoice {
  id: string;
  kind: "personal" | "shared";
  label: string;
  members?: number;
  slug?: string;
}

/** One plugin (or Workflows) on a team copy, as the Team modal lists it. */
export interface TeamPluginRow {
  pluginId: string;
  label: string;
  /** A data URL, or "" (Workflows and plugins with no icon). */
  icon: string;
  /** "store" | "local" | "ai" | "workflows". */
  source: string;
  incoming: number;
  incomingBy: string[];
  /** Incoming changes wait for Update now. */
  held: boolean;
  outgoing: PluginScopeOutgoing[];
  pending: number;
  conflicts: number;
  blocked: { kind: string; key: string; name?: string; error?: string; attempts?: number }[];
  state: string;
  error: string;
  /** Docs and files in the team copy. */
  items: number;
}

/** The Team modal's team copy: `current` is a space id, or "personal" when no plugin shows a team. */
export interface TeamOverview {
  ok?: boolean;
  current: string;
  label: string;
  plugins: TeamPluginRow[];
  error?: string;
}

export interface ContextBreakdownSubItem {
  label: string;
  tokens: number;
  sublabel?: string;
  /** Exact text this sub-item accounts for. Only present in an on-open, content-rich fetch. */
  content?: string;
}

export interface ContextBreakdownItem {
  id: string;
  label: string;
  tokens: number;
  color: string;
  items?: ContextBreakdownSubItem[];
  /** Exact text for a leaf item (e.g. Ducky personality). Content-rich fetch only. */
  content?: string;
  /** Skill selection is enabled but not sent until verse/device intent (see active_tokens). */
  gated?: boolean;
  active_tokens?: number;
  /** How the frozen prefix is cached for the active provider, if at all. */
  cache_mode?: "cached" | "implicit" | "local";
}

export interface TokenUsageCall {
  ts: number;
  step: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens?: number;
  cache_write_tokens?: number;
  cost_usd?: number | null;
}

export interface CodingAgentInfo {
  coding_agent: string;
  label: string;
  model: string;
  enabled: boolean;
  session_active: boolean;
  num_turns: number;
  context_tokens: number;
  has_run: boolean;
  permission_mode?: string;
  /** "Allow everything in this chat" from an approval card: set here (own) or by the chat that started this run. */
  allow_everything?: { on: boolean; own: boolean; from_title?: string };
  /** The chat's approval mode, in the words of its permissions button. */
  approvals?: { mode: ChatPermissionModeId; label: string; asks: boolean };
  available?: boolean;
  status?: string;
  logged_in?: boolean;
  skills?: string[];
}

export interface ContextUsage {
  used_tokens: number;
  context_limit: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens?: number;
  total_cache_read?: number;
  total_cache_write?: number;
  cache_hit_rate?: number;
  /** Cache hit rate across every logged call (total_cache_read / total_input), vs cache_hit_rate's last-call-only figure. */
  cache_hit_rate_cumulative?: number;
  /** Unix seconds when the agent's prompt cache goes cold; after it the next turn re-sends the history uncached. */
  cache_expires_at?: number | null;
  call_count?: number;
  calls?: TokenUsageCall[];
  cost_usd?: number | null;
  breakdown?: ContextBreakdownItem[];
  omitted?: string[];
  agent_info?: CodingAgentInfo;
}

export interface ProviderUsageDay {
  date: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  call_count: number;
  cost_usd: number;
}

export interface ProviderUsageModel {
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens?: number;
  cache_write_tokens?: number;
  call_count: number;
  cost_usd: number;
}

export interface ProviderUsageAgent {
  agent: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens?: number;
  cache_write_tokens?: number;
  call_count: number;
  cost_usd: number;
}

export interface ProviderUsageDucky {
  conv_id: string;
  label: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens?: number;
  cache_write_tokens?: number;
  call_count: number;
  cost_usd: number;
}

export interface ProviderUsageReport {
  provider: string;
  days: number;
  call_count: number;
  total_input: number;
  total_output: number;
  total_tokens: number;
  total_cache_read: number;
  total_cache_write: number;
  cache_hit_rate: number;
  cost_usd: number | null;
  by_day: ProviderUsageDay[];
  by_model: ProviderUsageModel[];
  by_agent?: ProviderUsageAgent[];
  by_ducky?: ProviderUsageDucky[];
  error?: string;
}

export interface DuckyUsageChat {
  conv_id: string;
  title: string;
  updated?: number;
}

export interface DuckyUsageReport {
  ducky_name: string;
  profile_id: string;
  days: number;
  chat_count: number;
  call_count: number;
  total_input: number;
  total_output: number;
  total_tokens: number;
  total_cache_read: number;
  total_cache_write: number;
  cost_usd: number | null;
  chats: DuckyUsageChat[];
  error?: string;
}

export interface ContextControlResult {
  ok: boolean;
  conv_id?: string;
  title?: string;
  cleared?: string[];
  restored?: string[];
  omitted?: string[];
}

export interface SessionFile {
  path: string;
  lines_added?: number;
  kind: "write" | "create" | "rename" | "move";
}

export type AppDataKind = "cache" | "user" | "install" | "runtime" | "settings" | "other";

export interface AppDataItem {
  name: string;
  rel: string;
  is_dir: boolean;
  bytes: number;
  files: number;
  dirs: number;
  kind: AppDataKind;
  label: string;
  description: string;
  clearable: boolean;
  deletable: boolean;
  protected: boolean;
}

export interface AppDataOverview {
  root: string;
  bytes: number;
  files: number;
  dirs: number;
  items: AppDataItem[];
}

export interface AppDataChildren {
  rel: string;
  items: AppDataItem[];
  truncated: boolean;
  total: number;
  error?: string;
}

export interface AppDataProjectArea {
  name: string;
  rel: string;
  bytes: number;
  files: number;
  dirs: number;
}

export interface AppDataProject {
  slug: string;
  label: string;
  path: string;
  bytes: number;
  areas: AppDataProjectArea[];
  /** ducky.db rows scoped to this project, by table (ADR 0003). */
  rows?: Record<string, number>;
}

/** Settings → General → App Data → Database (ADR 0003). */
export interface StoreTable {
  name: string;
  label: string;
  group: "chats" | "settings" | "changes" | "plans" | "verse" | "logs" | "search";
  group_label: string;
  description: string;
  rows: number;
  clearable: boolean;
}

export interface StoreSnapshot {
  name: string;
  bytes: number;
  ts: number;
}

export interface StoreLegacy {
  rel: string;
  bytes: number;
  files: number;
  stores: { name: string; bytes: number; files: number }[];
}

export interface StoreImporter {
  name: string;
  report: unknown;
  ts: number;
}

export interface StoreOverview {
  path: string;
  exists: boolean;
  sqlite_version: string;
  backends: Record<string, "rows" | "files">;
  size_bytes: number;
  wal_bytes: number;
  schema_version: number;
  head_version: number;
  page_size?: number;
  journal_mode?: string;
  tables: StoreTable[];
  snapshots: StoreSnapshot[];
  legacy: StoreLegacy | null;
  importers: StoreImporter[];
  integrity: { result: string; ts: number } | null;
  last_restore: string | null;
  restore_pending: boolean;
  clean_boots: number;
  clean_boots_needed: number;
  error: string;
}

/** Skill and tool search index (backend/search): Database tab card and search boxes. */
export interface SearchStatus {
  ok: boolean;
  error?: string;
  running: boolean;
  model: string;
  model_error: string;
  docs?: Partial<Record<"pack" | "subskill" | "section" | "tool" | "record", number>>;
  vectors?: Record<string, number>;
  last_indexed?: number;
  sources?: number;
}

export interface SearchHit {
  kind: "pack" | "subskill" | "section" | "tool" | "record";
  title: string;
  where: string;
  snippet: string;
  score: number;
  pack?: string;
  subskill?: string;
  section?: string;
  tool?: string;
  plugin?: string;
  read?: string;
}

export interface SearchQueryResult {
  ok: boolean;
  error?: string;
  results: SearchHit[];
}

export interface StoreTablePreview {
  ok: boolean;
  error?: string;
  table?: string;
  columns: string[];
  rows: Record<string, unknown>[];
  total: number;
  offset?: number;
  limit?: number;
}

export interface StoreActionResult {
  ok: boolean;
  error?: string;
  result?: string;
  snapshot?: string;
  staged?: string;
  restart_required?: boolean;
  removed?: number;
  removed_files?: number;
  bytes?: number;
  before?: number;
  after?: number;
  path?: string;
  rows?: number;
  reports?: Record<string, unknown>;
}

export interface AppDataActionResult {
  ok: boolean;
  error?: string;
  removed?: number;
  cleared?: string[];
}

/** One Windows dictation event from voice_win_stt_poll. */
export interface WinSttEvent {
  t: "started" | "interim" | "final" | "speech_started" | "speech_stopped" | "error" | "ended" | "exit";
  sid?: string;
  text?: string;
  code?: string;
  message?: string;
  status?: string;
}

export interface PanelApi {
  /** A file attached to an answer on an agent's question card, saved in that chat. */
  save_chat_file?(
    conv_id: string,
    name: string,
    data_base64: string,
  ): Promise<{ ok: boolean; path?: string; readable_path?: string; size_bytes?: number; error?: string }>;
  stage_video_attachment?(
    conv_id: string,
    name: string,
    mime: string,
    data_base64: string,
  ): Promise<{
    ok: boolean;
    error?: string;
    staged_id?: string;
    size_bytes?: number;
    mime?: string;
    needs_ffmpeg?: boolean;
    ffmpeg?: FfmpegStatusDto;
    prep?: VideoPrepStatusDto;
  }>;
  get_video_prep_status?(stagedIds?: string[]): Promise<{ ok: boolean; prep: Record<string, VideoPrepStatusDto> }>;
  retry_video_prep?(stagedId: string): Promise<{ ok: boolean; prep: VideoPrepStatusDto }>;
  get_video_settings?(convId?: string, model?: string, codingAgent?: string): Promise<VideoSettingsDto>;
  set_video_settings?(patch: Partial<Omit<VideoSettingsDto, "ffmpeg" | "ok">>): Promise<VideoSettingsDto>;
  get_ffmpeg_status?(): Promise<FfmpegStatusDto>;
  install_ffmpeg?(): Promise<FfmpegStatusDto>;
  remove_ffmpeg?(): Promise<FfmpegStatusDto>;
  get_listener_status(): Promise<ListenerStatus>;
  get_window_bounds(): Promise<{ x: number; y: number; width: number; height: number; scale?: number }>;
  set_window_bounds(x: number, y: number, width: number, height: number): Promise<void>;
  set_sidebar_only_layout(enabled: boolean): Promise<void>;
  dock_focus_window_beside_main(): Promise<void>;
  uses_native_window_chrome(): Promise<boolean>;
  begin_native_window_move(): Promise<boolean>;
  begin_native_window_resize(edge: string): Promise<boolean>;
  open_focus_window(focus_id: string, title: string, solo?: boolean): Promise<void>;
  /** False when the drop landed back in this window — the caller keeps the tab. */
  open_focus_window_at_point(focus_id: string, title: string, screen_x: number, screen_y: number): Promise<boolean>;
  open_focus_window_group?(tabs: { focus_id: string; title: string }[]): Promise<void>;
  list_focus_tab_ids?(): Promise<string[]>;
  adopt_tab_into_this_focus_window(focus_id: string, title: string, wid?: string): Promise<void>;
  raise_focus_window(focus_id: string): Promise<void>;
  browser_pane_open?(pane_id: string, url?: string, wid?: string): Promise<BrowserPaneState>;
  browser_pane_set_bounds?(
    pane_id: string,
    x: number,
    y: number,
    width: number,
    height: number,
    viewport_w?: number,
    viewport_h?: number,
    visible?: boolean,
  ): Promise<{ ok?: boolean; error?: string }>;
  browser_pane_navigate?(pane_id: string, url: string): Promise<{ ok?: boolean; error?: string }>;
  browser_pane_command?(pane_id: string, command: string): Promise<{ ok?: boolean; error?: string }>;
  browser_pane_state?(pane_id: string): Promise<BrowserPaneState>;
  browser_pane_close?(pane_id: string): Promise<{ ok?: boolean; closed?: boolean }>;
  /** JPEG data URL of what the pane shows, painted in its place under HTML overlays. */
  browser_pane_snapshot?(pane_id: string): Promise<{ ok?: boolean; data?: string; error?: string }>;
  browser_pane_list?(): Promise<{
    ok?: boolean;
    panes?: BrowserPaneState[];
    pane_count?: number;
  }>;
  /** Emergency: hide every native browser pane (stuck overlay / click-steal recovery). */
  browser_pane_hide_all?(): Promise<{ ok?: boolean; hidden?: number }>;
  browser_clear_browsing_data?(kinds?: string): Promise<{
    ok?: boolean;
    via?: string;
    kinds?: string;
    removed?: string[];
    errors?: string[];
    error?: string;
  }>;
  browser_runtime_info?(): Promise<{
    ok?: boolean;
    engine?: string;
    browser_version?: string;
    chrome_major?: string;
    user_agent?: string;
    user_data_dir?: string;
    ebwebview_dir?: string;
    isolation?: string;
    secure_profile?: boolean;
    https_only_nav?: boolean;
    self_embed_blocked?: boolean;
    protections?: string[];
  }>;
  browser_site_security?(pane_id?: string): Promise<{
    ok?: boolean;
    error?: string;
    pane_id?: string;
    site?: {
      url?: string;
      title?: string;
      host?: string;
      scheme?: string;
      connection?: string;
      headline?: string;
      detail?: string;
      certificate?: string;
      cert_error?: string;
      cookie_count?: number;
      cookies?: Array<{
        name?: string;
        domain?: string;
        path?: string;
        secure?: boolean;
        http_only?: boolean;
        session?: boolean;
      }>;
      is_playing_audio?: boolean;
      is_muted?: boolean;
    };
    runtime?: Record<string, unknown>;
    protections?: string[];
  }>;
  notify_focus_tab_active(focus_id: string, title: string): Promise<void>;
  report_focus_window_layout(birth_tab_id: string, layout: EditorLayoutState): Promise<void>;
  return_tab_to_main(focus_id: string, title: string, wid?: string): Promise<boolean>;
  close_focus_window(focus_id: string, reason?: string, wid?: string): Promise<void>;
  close_all_focus_windows(): Promise<void>;
  get_editor_workspace(slug?: string): Promise<EditorWorkspaceSnapshot>;
  save_editor_workspace(payload: EditorWorkspaceSnapshot): Promise<void>;
  /** Durable left/right dock rails (AppData). Empty object = nothing saved yet. */
  get_workspace_dock?(window_id?: string): Promise<Record<string, unknown>>;
  save_workspace_dock?(payload: {
    window_id?: string;
    snapshot: Record<string, unknown>;
  }): Promise<void>;
  get_header_visibility?(): Promise<{ hidden?: string[]; applied?: string[] }>;
  save_header_visibility?(payload: { hidden: string[]; applied?: string[] }): Promise<void>;
  restore_focus_windows(groups?: FocusWindowSnapshot[]): Promise<void>;
  report_editor_state(relative_path: string, state: Record<string, unknown>): Promise<void>;
  /** `wid` = this window's registry id; without it the host closes the ACTIVE window. */
  /** `returnTabs`: hand this window's tabs back to the main window (its close button). */
  close_this_window(reason?: string, wid?: string, returnTabs?: boolean): Promise<void>;
  is_focus_window(): Promise<boolean>;
  toggle_maximize(): Promise<boolean>;
  is_window_maximized?(): Promise<boolean>;
  minimize_window(): Promise<void>;
  get_version(): Promise<string>;
  get_app_update_status(): Promise<AppUpdateStatus>;
  get_app_patch_notes(): Promise<{
    versions: DuckyOSStorePatchNote[];
    cached: boolean;
    error?: string | null;
  }>;
  get_whats_new?(): Promise<WhatsNewStatus>;
  get_auto_update_status?(): Promise<AutoUpdateStatus>;
  report_update_ui?(state: UpdateUiReport): Promise<void>;
  apply_auto_update_now?(): Promise<{ ok: boolean; error?: string; waiting_for?: string[] }>;
  check_auto_update_now?(): Promise<AutoUpdateStatus>;
  mark_whats_new_seen?(version: string): Promise<{ ok: boolean; error?: string }>;
  get_install_info(): Promise<InstallInfo>;
  apply_update(): Promise<UpdaterResult>;
  get_update_progress(): Promise<UpdateProgress>;
  cancel_update(): Promise<{ ok: boolean }>;
  launch_uninstall(): Promise<UpdaterResult>;
  open_download_page(): Promise<void>;
  open_external_url(url: string): Promise<void>;
  /** Open WebView2 DevTools / Inspector (production-safe). */
  open_devtools?(): Promise<{ ok: boolean; error?: string }>;
  /** One line in Settings → Errors from the page (scrubbed like every log). */
  report_ui_error?(source: string, message: string): Promise<{ ok: boolean }>;
  /** Crash reports kept on this PC, newest first, with the exact text each one sends. */
  crash_reports?(): Promise<CrashReportsState>;
  crash_report_send?(reportId: string): Promise<{ ok: boolean; error?: string }>;
  crash_report_delete?(reportId: string): Promise<{ ok: boolean }>;
  /** Download a Google font once into AppData; `href` is its stylesheet on the local panel server. */
  cache_font?(family: string): Promise<{ ok: boolean; href?: string; error?: string }>;
  /** Persist ErrorBoundary crash details to AppData/ui_crashes.jsonl. */
  report_ui_crash?(payload: {
    label?: string;
    message?: string;
    stack?: string;
    componentStack?: string;
    appVersion?: string;
    pluginId?: string;
    surface?: string;
    faultKind?: string;
    faultAction?: string;
  }): Promise<{ ok: boolean; path?: string; error?: string }>;
  duckyos_get_status(): Promise<DuckyOSAccountStatus>;
  duckyos_agent_caps?(include_catalog?: boolean): Promise<{
    ok?: boolean;
    denied?: string[];
    settings?: AgentCapsSettings;
    denied_count?: number;
    catalog?: {
      categories?: Array<{
        id?: string;
        label?: string;
        tools?: Array<{ name?: string; description?: string; destructive?: boolean }>;
      }>;
    };
  }>;
  duckyos_agent_caps_set?(
    denied?: string[],
    settings?: AgentCapsSettings,
  ): Promise<{
    ok?: boolean;
    denied?: string[];
    settings?: AgentCapsSettings;
    denied_count?: number;
    catalog?: {
      categories?: Array<{
        id?: string;
        label?: string;
        tools?: Array<{ name?: string; description?: string; destructive?: boolean }>;
      }>;
    };
  }>;
  duckyos_login(base_url?: string): Promise<DuckyOSAccountStatus>;
  duckyos_cancel_login(): Promise<DuckyOSAccountStatus>;
  /** @deprecated Verification happens in the browser. */
  duckyos_submit_code(code?: string): Promise<DuckyOSAccountStatus>;
  duckyos_logout(): Promise<DuckyOSAccountStatus>;
  duckyos_list_pcs?(): Promise<{
    ok?: boolean;
    error?: string;
    devices?: Array<{ keyId?: string; name?: string; live?: boolean; last_seen?: number; mine?: boolean }>;
  }>;
  duckyos_revoke_pc?(key_id: string): Promise<DuckyOSAccountStatus & { ok?: boolean; error?: string }>;
  /** Your account's UEFN Duckies (`mine` = this one; `live` = reachable now). This PC's windows only. */
  ducky_link_list?(): Promise<{
    ok?: boolean;
    error?: string;
    devices?: Array<{ keyId?: string; name?: string; live?: boolean; last_seen?: number; mine?: boolean }>;
  }>;
  /** One allowlisted call (list_all_conversations, load_messages, send_message…) on another of your UEFN Duckies. */
  ducky_link_call?(to: string, method: string, args?: Record<string, unknown>): Promise<{ ok?: boolean; result?: unknown; error?: string }>;
  /** Open another of your UEFN Duckies in its own window here: its whole app, chats included (at `chat_id`). */
  ducky_link_open?(key_id: string, name?: string, chat_id?: string): Promise<{ ok?: boolean; error?: string }>;
  /** Your other UEFN Duckies' chats as copied here by sync (no rows). */
  ducky_sync_list?(): Promise<{ ok?: boolean; error?: string; duckies?: DuckySyncPeer[] }>;
  /** Answer the right-click menu over typed text: run WebView2's command (a spelling fix...) or -1 for none. */
  native_menu_pick?(token: string, command_id: number): Promise<{ ok?: boolean }>;
  /** Remove another UEFN Ducky: signs that PC out of the account and drops its copies here. */
  ducky_sync_forget?(key_id: string): Promise<{ ok?: boolean; error?: string; dropped?: number; revoke_error?: string }>;
  /** The read-only copy of one of another Ducky's chats. */
  ducky_sync_copy?(key_id: string, chat_id: string): Promise<{
    ok?: boolean;
    error?: string;
    title?: string;
    updated?: number;
    synced?: number;
    rows?: Array<{ id?: number | string; role?: string; text?: string }>;
  }>;
  /** Pull from your other UEFN Duckies now. */
  ducky_sync_now?(): Promise<{ ok?: boolean }>;
  duckyos_open_admin(): Promise<void>;
  /** Opens a path of the signed-in account's website. */
  duckyos_open_site?(path?: string): Promise<{ ok?: boolean; url?: string; error?: string }>;
  remote_status?(): Promise<RemoteAccessStatus>;
  remote_set_enabled?(enabled: boolean): Promise<RemoteAccessStatus>;
  remote_sign_out_all?(): Promise<RemoteAccessStatus>;
  duckyos_store_catalog?(): Promise<DuckyOSStoreCatalog>;
  duckyos_store_versions?(slug: string, latestVersion?: string): Promise<DuckyOSStoreVersions>;
  duckyos_store_download?(
    slug: string,
    version?: string,
    isUpdate?: boolean,
  ): Promise<DuckyOSStoreInstallResult>;
  starter_llm_onboard_pending?(): Promise<{
    ok?: boolean;
    pending?: boolean;
    grandfathered?: boolean;
    error?: string;
  }>;
  /** Finish the first-run setup: makes the welcome ducky (once per AppData). */
  finish_first_run_setup?(): Promise<{
    ok?: boolean;
    created?: boolean;
    id?: string;
    title?: string;
    error?: string;
  }>;
  starter_setup_status?(): Promise<{
    ok?: boolean;
    /** The first-run setup is open: a fresh install's, until Finish. */
    pending_first_run?: boolean;
    gateway_ids?: string[];
    /** The first-run bundle, in install order, with what is on disk now. */
    plugins?: StarterPluginDto[];
    error?: string;
  }>;
  ensure_starter_llm_gateways?(force?: boolean): Promise<{
    ok?: boolean;
    first_run?: boolean;
    grandfathered?: boolean;
    installed?: string[];
    skipped?: string[];
    present?: string[];
    errors?: Array<{ slug: string; error: string; code: string }>;
    error?: string;
  }>;
  list_uefn_plugins?(): Promise<{ ok?: boolean; error?: string; plugins?: UefnPluginDto[] }>;
  /** `icons[plugin_id]`: a plugin's icon, sent once instead of on each of its nodes. */
  list_workflow_nodes?(): Promise<{ ok?: boolean; nodes?: AutomationNodeDto[]; icons?: Record<string, string> }>;
  list_generated_images?(): Promise<{
    ok?: boolean;
    images?: Array<{ name: string; media_url?: string; prompt?: string; gateway?: string; model?: string }>;
  }>;
  get_generated_image_attachment?(name: string): Promise<{
    ok?: boolean;
    error?: string;
    attachment?: MessageAttachmentDto;
  }>;
  list_workflow_templates?(): Promise<{ ok?: boolean; templates?: AutomationTemplateDto[] }>;
  save_workflow_template?(
    name: string,
    description?: string,
    icon?: string,
    graph_json?: string,
    template_id?: string,
    category?: string,
    /** With ``folder``: keep that folder of ``owner`` (nested folders, every workflow) as a folder template. */
    owner?: string,
    folder?: string,
  ): Promise<{ ok?: boolean; error?: string; template?: AutomationTemplateDto }>;
  delete_workflow_template?(template_id: string): Promise<{ ok?: boolean; error?: string }>;
  use_workflow_template?(template_id: string, owner?: string, parent_path?: string, name?: string): Promise<WorkflowTemplateUseDto>;
  list_workflow_versions?(workflowId: string): Promise<{ ok?: boolean; versions?: { id: string; name: string; saved_at: number; node_count: number; note?: string }[]; error?: string }>;
  get_workflow_version?(workflowId: string, versionId: string): Promise<{ ok?: boolean; workflow?: AutomationDto; error?: string }>;
  list_workflows?(): Promise<{ ok?: boolean; workflows?: AutomationSummaryDto[] }>;
  workflow_owners?(refresh?: boolean): Promise<WorkflowOwnersDto>;
  workflow_sync?(force?: boolean, space_id?: string, upload?: boolean): Promise<{ ok?: boolean; started?: boolean; error?: string }>;
  import_local_workflows?(): Promise<{ ok?: boolean; moved?: number; error?: string }>;
  /** Opens the shared space's Workflows page on the web. */
  workflow_open_web?(space_id: string): Promise<{ ok?: boolean; url?: string; error?: string }>;
  get_workflow?(workflow_id: string): Promise<{ ok?: boolean; error?: string; workflow?: AutomationDto }>;
  /** base_updated: the `updated` of the copy the editor changed; a newer stored one is
   *  kept and comes back with conflict: true instead of being overwritten. */
  save_workflow?(doc: Partial<AutomationDto> & { graph?: AutomationGraphDto; base_updated?: number }, owner?: string): Promise<{
    ok?: boolean;
    error?: string;
    conflict?: boolean;
    workflow?: AutomationDto;
  }>;
  copy_workflow?(workflow_id: string, owner: string, move?: boolean): Promise<{ ok?: boolean; error?: string; workflow?: AutomationDto }>;
  clear_workflow_runs?(workflow_id: string): Promise<{ ok?: boolean; error?: string }>;
  set_workflow_run_here?(workflow_id: string, on: boolean): Promise<{ ok?: boolean; error?: string; workflow?: AutomationDto }>;
  set_workflow_folder?(workflow_id: string, folder: string): Promise<{ ok?: boolean; error?: string; workflow?: AutomationDto }>;
  /** Rename or move a folder; its parent as new_path deletes it and keeps the workflows. */
  move_workflow_folder?(owner: string, path: string, new_path: string): Promise<{ ok?: boolean; error?: string; moved?: number }>;
  /** Make a folder that holds nothing yet; returns the owner's folders. */
  add_workflow_folder?(owner: string, path: string): Promise<{ ok?: boolean; error?: string; folders?: string[] }>;
  export_workflow_folder?(owner: string, path: string): Promise<{ ok?: boolean; error?: string; bundle?: WorkflowBundleDto }>;
  import_workflow_bundle?(bundle: WorkflowBundleDto, owner?: string, parent_path?: string, name?: string): Promise<WorkflowFolderCopyDto>;
  /** Copy (new ids, calls re-pointed) or move (same ids) a whole folder tree to Local or a shared space. */
  copy_workflow_folder?(owner_from: string, path: string, owner_to: string, parent_path?: string, move?: boolean): Promise<WorkflowFolderCopyDto>;
  delete_workflow?(workflow_id: string): Promise<{ ok?: boolean; error?: string }>;
  /** Stop button: ends every run of this workflow on this PC now. */
  stop_workflow?(workflow_id: string, run_id?: string): Promise<{ ok?: boolean; stopped?: boolean; error?: string }>;
  dismiss_workflow_run?(chat_id: string, run_id: string): Promise<{ ok?: boolean; error?: string }>;
  workflow_run_snapshot?(): Promise<{ ok?: boolean; events?: PanelPushEvent[]; dismissed?: { chat_id: string; run_id: string }[] }>;
  /** The Workflows editor's grid, snap, tool, panel sizes and zoom, kept on disk. */
  workflow_editor_prefs?(): Promise<{ ok?: boolean; prefs?: Record<string, unknown> }>;
  set_workflow_editor_prefs?(prefs: Record<string, unknown>): Promise<{ ok?: boolean; prefs?: Record<string, unknown> }>;
  /** Input nodes: pick files on this PC (accept = image | audio | video | mesh | pdf | svg | any). */
  pick_workflow_files?(accept: string, multiple: boolean): Promise<{ ok?: boolean; files?: FileRefDto[]; error?: string }>;
  /** Real paths of files just dropped from Explorer onto a plugin panel, handed out once. */
  consume_dropped_file_paths?(names: string[]): Promise<{ ok?: boolean; files?: FileRefDto[] }>;
  /** Save file nodes: the Windows folder picker. */
  pick_workflow_folder?(): Promise<{ ok?: boolean; folder?: string; error?: string }>;
  /** Run one node now; what feeds it is reused from the last run. */
  /** approveSpend: the person pressed play, so paid steps this run needs may spend. */
  run_workflow_node?(workflowId: string, nodeId: string, approveSpend?: boolean): Promise<AutomationRunDto>;
  /** A Preview's "Use this": run the steps that take what it shows, with that value. */
  keep_workflow_preview?(workflowId: string, nodeId: string): Promise<AutomationRunDto>;
  /** '' when an If / Expression condition parses, else what is wrong. */
  check_workflow_expression?(expression: string): Promise<{ ok?: boolean; error?: string }>;
  /** Custom code: the JavaScript a node runs (a built-in's generated from the settings of `node`,
   *  the editor's draft of it, else its saved ones). */
  get_workflow_node_code?(workflowId: string, nodeId: string, node?: AutomationGraphNodeDto | null): Promise<WorkflowNodeCodeDto>;
  /** Custom code: parse the declaration and check the code without saving it. */
  check_workflow_node_code?(code: string): Promise<CodeCheckDto>;
  /** Custom code: run one node's code (the unsaved draft when `code` is given) without saving;
   *  a dry run records its tool and built-in calls instead of making them. */
  test_workflow_node?(
    workflowId: string,
    nodeId: string,
    code?: string | null,
    inputs?: Record<string, unknown> | null,
    settings?: Record<string, unknown> | null,
    dryRun?: boolean,
  ): Promise<CodeTestResultDto>;
  /** Custom code: a person read this code, so it may run unattended on this PC. */
  approve_workflow_node_code?(workflowId: string, nodeId: string, codeSha: string): Promise<{ ok?: boolean; error?: string }>;
  /** Custom code: the `ducky` object's types (module "ducky") and typed args for the named tools. */
  workflow_code_api?(tools?: string[] | null): Promise<WorkflowCodeApiDto>;
  run_workflow?(
    workflow_id: string,
    prompt?: string,
    files?: unknown[],
    caller_conv_id?: string,
    payload?: Record<string, unknown>,
    trigger_id?: string,
    starter_id?: string,
  ): Promise<AutomationRunDto>;
  emit_workflow_trigger?(
    trigger_id: string,
    payload?: Record<string, unknown>,
  ): Promise<{ ok?: boolean; error?: string; trigger_id?: string; runs?: AutomationRunDto[] }>;
  get_uefn_plugin_contributions?(): Promise<UefnPluginContributionsDto>;
  set_uefn_plugin_enabled?(
    plugin_id: string,
    enabled: boolean,
    trust_local?: boolean,
  ): Promise<{ ok?: boolean; error?: string; needs_trust?: boolean; enabled?: boolean }>;
  /** Standalone Store/user skill packs — global deny-list toggle (Settings → Store). */
  set_skill_pack_enabled?(
    pack_id: string,
    enabled: boolean,
  ): Promise<{ ok?: boolean; error?: string; enabled?: boolean; pack_id?: string }>;
  get_uefn_plugin_secret_labels?(
    plugin_id: string,
  ): Promise<{ ok?: boolean; error?: string; secret_keys?: string[]; labels?: string[] }>;
  uninstall_uefn_plugin?(
    plugin_id: string,
    erase_data?: boolean,
  ): Promise<{ ok?: boolean; error?: string; erase_data?: boolean }>;
  install_uefn_plugin_bytes?(b64: string, source?: string): Promise<{ ok?: boolean; error?: string; id?: string }>;
  open_uefn_plugins_folder?(): Promise<void>;
  window_spotlight_replay(args: Record<string, unknown>): Promise<{ ok?: boolean; error?: string; shown?: boolean }>;
  snip_screen?(conv_id?: string): Promise<{
    ok?: boolean;
    reason?: string;
    error?: string;
    data_base64?: string;
    mime?: string;
    width?: number;
    height?: number;
    name?: string;
    /** UEFN project Saved/DuckyCaptures path (use this for file work). */
    path?: string;
    /** AppData panel snips path — preview-only; agents should not rely on it. */
    capture_path?: string;
  }>;
  hide_window(): Promise<void>;
  list_folders(all_projects?: boolean): Promise<FolderDto[]>;
  list_conversations(folder_id: string): Promise<{ id: string; title: string; sort_order: number; updated?: number; ducky_style?: string; ducky_name?: string; ducky_personality?: string; tts_voice?: string; tts_speed?: number; file_path?: string; model?: string; provider?: string; coding_agent?: string; thinking_effort?: string; terminal_session_id?: string; folder_id?: string; parent_conv_id?: string; is_group?: boolean; is_subagent?: boolean; group_members?: GroupMemberDto[]; tool_call_count?: number; file_count?: number; context_tokens?: number }[]>;
  list_all_conversations(all_projects?: boolean): Promise<{ id: string; title: string; sort_order: number; updated?: number; ducky_style?: string; ducky_name?: string; profile_id?: string; ducky_personality?: string; tts_voice?: string; tts_speed?: number; file_path?: string; model?: string; provider?: string; coding_agent?: string; thinking_effort?: string; terminal_session_id?: string; folder_id?: string; parent_conv_id?: string; is_group?: boolean; is_subagent?: boolean; group_members?: GroupMemberDto[]; tool_call_count?: number; file_count?: number; context_tokens?: number; project_slug?: string; project_name?: string }[]>;
  list_conversations_for_file(file_path: string): Promise<{ id: string; title: string; sort_order: number; updated?: number; ducky_style?: string; ducky_name?: string; ducky_personality?: string; tts_voice?: string; tts_speed?: number; file_path?: string; model?: string; provider?: string; coding_agent?: string; thinking_effort?: string; terminal_session_id?: string; folder_id?: string; parent_conv_id?: string; is_group?: boolean; is_subagent?: boolean; group_members?: GroupMemberDto[]; tool_call_count?: number; file_count?: number; context_tokens?: number }[]>;
  apply_sidebar_layout(patch: SidebarLayoutPatch): Promise<void>;
  create_folder(name: string, parent_id?: string): Promise<{ id: string; name: string }>;
  rename_folder(folder_id: string, name: string): Promise<void>;
  /** Resolves to the group hub chat ids moved to Archive. archiveMembers also archives duckies. */
  delete_folder(folder_id: string, archiveMembers?: boolean): Promise<string[]>;
  create_conversation(
    folder_id: string,
    ducky_style?: string,
    file_path?: string,
    config?: DuckyConfigDto,
  ): Promise<{ id: string; title: string; ducky_style?: string; ducky_name?: string; ducky_personality?: string; file_path?: string; model?: string; provider?: string; coding_agent?: string }>;
  apply_ducky_config(conv_id: string, config: DuckyConfigDto): Promise<{ ok: boolean; error?: string }>;
  group_create?(name?: string, folder_id?: string, open_tab?: boolean, project_slug?: string): Promise<{ ok?: boolean; id?: string; title?: string; is_group?: boolean; leader_conv_id?: string; group_members?: GroupMemberDto[]; folder_id?: string; error?: string }>;
  group_invite?(
    group_id: string,
    profile_id: string,
    model?: string,
  ): Promise<{ ok?: boolean; member?: GroupMemberDto; group_members?: GroupMemberDto[]; leader_conv_id?: string; error?: string }>;
  group_set_leader?(
    group_id: string,
    member_conv_id: string,
  ): Promise<{ ok?: boolean; leader_conv_id?: string; group_members?: GroupMemberDto[]; error?: string }>;
  group_set_member_model?(
    group_id: string,
    member_conv_id: string,
    model?: string,
  ): Promise<{ ok?: boolean; group_members?: GroupMemberDto[]; error?: string }>;
  group_set_member_lane?(
    group_id: string,
    member_conv_id: string,
    write_allowed: string[] | null,
    force?: boolean,
  ): Promise<{ ok?: boolean; group_members?: GroupMemberDto[]; warnings?: string[]; lanes?: LaneSetDto; error?: string }>;
  group_get_lanes?(group_id: string): Promise<{ ok?: boolean; error?: string } & Partial<LaneSetDto>>;
  group_check_lane?(group_id: string, member_conv_id: string, write_allowed: string[] | null): Promise<LaneCheckResult>;
  group_remove?(group_id: string, member_conv_id: string): Promise<{ ok?: boolean; group_members?: GroupMemberDto[]; leader_conv_id?: string; error?: string }>;
  group_members?(group_id: string): Promise<{ ok?: boolean; is_group?: boolean; leader_conv_id?: string; members?: GroupMemberDto[]; error?: string }>;
  /** Mute or unmute a chat or a group hub; only the flag, the agent keeps running. */
  set_muted?(conv_id: string, muted: boolean): Promise<{ ok?: boolean; id?: string; muted?: boolean; is_group?: boolean; error?: string }>;
  /** Stop every running member of this group and its nested groups. */
  group_stop?(group_id: string): Promise<{ ok?: boolean; stopped?: number; groups?: number; stopped_ids?: string[]; error?: string }>;
  group_add_member?(
    group_id: string,
    conv_id: string,
    as_leader?: boolean,
  ): Promise<{ ok?: boolean; leader_conv_id?: string; group_members?: GroupMemberDto[]; error?: string }>;
  list_agent_profiles(): Promise<AgentProfileListDto>;
  archive_agent_profile?(profile_id: string): Promise<{ ok: boolean; profile?: AgentProfileDto }>;
  unarchive_agent_profile?(profile_id: string): Promise<{ ok: boolean; profile?: AgentProfileDto }>;
  save_agent_profile(profile: AgentProfileDto): Promise<{ ok: boolean; profile: AgentProfileDto }>;
  save_agent_profile_override(bundled_id: string, patch: Partial<AgentProfileDto>): Promise<{ ok: boolean; profile: AgentProfileDto }>;
  delete_agent_profile(profile_id: string): Promise<{ ok: boolean }>;
  duplicate_agent_profile(profile_id: string): Promise<{ ok: boolean; profile: AgentProfileDto }>;
  get_agent_profile_editor_catalog(): Promise<AgentProfileEditorCatalogDto>;
  set_conversation_ducky_style(conv_id: string, ducky_style: string, ducky_personality?: string): Promise<void>;
  list_ducky_catalog(): Promise<DuckyCatalogDto>;
  upload_custom_ducky(filename: string, png_base64: string): Promise<DuckyDto>;
  delete_custom_ducky(style_id: string): Promise<{ ok: boolean }>;
  open_custom_duckies_folder(): Promise<void>;
  list_custom_verse_templates(): Promise<VerseTemplateDto[]>;
  get_custom_verse_template?(template_id: string): Promise<VerseTemplateDto | null>;
  save_custom_verse_template(
    name: string,
    icon: string,
    content?: string,
    template_id?: string,
    folder?: string,
    files_json?: string,
  ): Promise<VerseTemplateDto>;
  delete_custom_verse_template(template_id: string): Promise<{ ok: boolean }>;
  rename_conversation(conv_id: string, title: string): Promise<void>;
  set_agent_allow_everything?(conv_id: string, on: boolean): Promise<{ ok: boolean; on: boolean; own: boolean; from_title?: string }>;
  get_agent_permissions?(conv_id: string, agent?: string): Promise<ChatPermissionsDto>;
  set_agent_permission_mode?(conv_id: string, mode: ChatPermissionModeId, agent?: string): Promise<ChatPermissionsDto>;
  remove_agent_permission_rule?(conv_id: string, rule: string, agent?: string): Promise<ChatPermissionsDto>;
  clear_agent_permission_rules?(conv_id: string, agent?: string): Promise<ChatPermissionsDto>;
  update_agent_plugin?(conv_id: string, agent?: string): Promise<ChatPermissionsDto>;
  list_saved_agent_permissions(): Promise<SavedAgentPermissionDto[]>;
  revoke_saved_agent_permission(conv_id: string, rule: string): Promise<{ ok: boolean }>;
  list_gateway_accounts?(provider: string): Promise<
    GatewayAccountResult<{ accounts: GatewayAccountDto[]; auto_switch: boolean; /** Account for new chats. */ default?: string }>
  >;
  gateway_account_usage?(
    provider: string,
    account: string,
    refresh?: boolean,
  ): Promise<GatewayAccountResult<{ windows?: UsageWindowDto[]; notice?: string }>>;
  /** This chat runs as ``account`` from now on ("" = follow the default). */
  set_chat_account?(conv_id: string, provider: string, account: string): Promise<GatewayAccountResult>;
  get_chat_account?(
    conv_id: string,
    provider: string,
  ): Promise<GatewayAccountResult<{ account: string; label: string; follows_default: boolean }>>;
  add_gateway_account?(provider: string, label: string, kind: "key" | "login"): Promise<GatewayAccountResult<{ id: string }>>;
  rename_gateway_account?(provider: string, account: string, label: string): Promise<GatewayAccountResult>;
  remove_gateway_account?(provider: string, account: string): Promise<GatewayAccountResult>;
  reorder_gateway_accounts?(provider: string, ids: string[]): Promise<GatewayAccountResult>;
  set_gateway_account?(provider: string, account: string): Promise<GatewayAccountResult>;
  set_gateway_account_key?(provider: string, account: string, key: string): Promise<GatewayAccountResult>;
  set_gateway_auto_switch?(provider: string, on: boolean): Promise<GatewayAccountResult>;
  login_gateway_account?(provider: string, account: string, conv_id?: string): Promise<GatewayAccountResult<{ session_id: string }>>;
  logout_gateway_account?(provider: string, account: string): Promise<GatewayAccountResult>;
  gateway_account_status?(provider: string, account: string): Promise<GatewayAccountResult<{ status: GatewayAccountStatusDto }>>;
  move_conversation(conv_id: string, folder_id: string): Promise<void>;
  move_chats_to_project?(
    conv_ids: string[],
    folder_ids: string[],
    target_slug: string,
    folder_id?: string,
  ): Promise<void>;
  /** keepInside: only this group goes; what was in it stays, on its own. */
  delete_conversation(conv_id: string, keepInside?: boolean): Promise<void>;
  /** `limit`: only the newest rows (before `before_id`); without it the whole chat. */
  load_messages(conv_id: string, before_id?: number | null, limit?: number): Promise<ChatMessage[]>;
  /** The whole result of a tool row `load_messages` cut. */
  load_tool_result?(conv_id: string, row_id: number): Promise<{ ok: boolean; result?: string; error?: string }>;
  get_settings(): Promise<PanelSettingsDto>;
  get_appearance(): Promise<AppearanceDto>;
  save_appearance(patch: AppearanceDto): Promise<string>;
  push_appearance_live(appearance: AppearanceDto): Promise<void>;
  save_appearance_profile(name: string): Promise<AppearanceProfileDto>;
  load_appearance_profile(profile_id: string): Promise<AppearanceDto>;
  /** Copy a user-picked audio file into AppData/sounds; returns stored filename. */
  import_sound_file?(): Promise<{ ok?: boolean; filename?: string; error?: string }>;
  rename_appearance_profile(profile_id: string, name: string): Promise<AppearanceProfileDto>;
  delete_appearance_profile(profile_id: string): Promise<string>;
  update_active_appearance_profile(): Promise<AppearanceProfileDto | null>;
  get_project_info(): Promise<ProjectInfo>;
  list_project_files(relative_path?: string): Promise<ProjectFileListing>;
  list_workspace_roots(): Promise<WorkspaceRootEntry[]>;
  list_project_file_paths(): Promise<{ path: string; name: string }[]>;
  search_workspace(
    query: string,
    scope?: WorkspaceSearchScope,
    case_sensitive?: boolean,
    whole_word?: boolean,
    max_results?: number,
  ): Promise<WorkspaceSearchResult>;
  replace_workspace(
    query: string,
    replacement: string,
    case_sensitive?: boolean,
    whole_word?: boolean,
  ): Promise<WorkspaceReplaceResult>;
  read_project_file(relative_path: string): Promise<{
    path: string;
    content: string;
    kind?: string;
    mime?: string;
    media_url?: string;
    media_base_url?: string;
    media_filename?: string;
    binary_preview?: string;
    size?: string;
  }>;
  classify_project_file?(relative_path: string): Promise<{
    kind: string;
    mime: string;
    editable: boolean;
    name: string;
  }>;
  project_file_media_url?(relative_path: string): Promise<{
    path: string;
    media_url: string;
    media_base_url?: string;
    media_filename?: string;
    mime: string;
    kind: string;
  }>;
  stat_project_file(relative_path: string): Promise<{ path: string; mtime_ns: number; size: number; exists: boolean }>;
  fingerprint_project_dirs(relative_paths: string[]): Promise<{ fingerprints: Record<string, string> }>;
  create_project_folder(parent_relative: string, name: string): Promise<{ path: string }>;
  copy_project_entry(source_relative: string, dest_parent_relative: string): Promise<{ path: string }>;
  move_project_entry(source_relative: string, dest_parent_relative: string): Promise<{ path: string }>;
  delete_project_entry(
    relative_path: string,
  ): Promise<{ path: string; trash_token?: string; name?: string }>;
  /** Ctrl+Z undo of a delete — restores the trashed file/folder to Content. */
  restore_trashed_entry(trash_token: string): Promise<{ path: string }>;
  rename_project_entry(source_relative: string, new_name: string): Promise<{ path: string }>;
  create_project_verse_file(parent_relative: string, name: string, content?: string): Promise<{ path: string }>;
  create_project_file(parent_relative: string, name: string, content?: string): Promise<{ path: string }>;
  /** Report the Content folder an external OS-file drag is over ("" = none), read by the native drop handler. */
  set_import_drop_target(dest_parent_relative: string): Promise<void>;
  /** Absolute paths queued from Open-with / CLI / second-instance handoff (consume once on mount). */
  consume_pending_open_files(): Promise<string[]>;
  /** uefn-ducky:// URLs queued from a browser protocol launch (consume once on mount). */
  consume_pending_deep_links(): Promise<string[]>;
  /** Copy files/folders from absolute OS paths into a Content folder (drop-to-import). */
  import_external_entries(
    dest_parent_relative: string,
    source_paths: string[],
  ): Promise<{ paths: string[]; errors: string[] }>;
  write_project_file(relative_path: string, content: string): Promise<{ path: string; bytes_written: number }>;
  record_external_file_change(
    relative_path: string,
    previous_content: string,
    new_content: string,
  ): Promise<{ ok: boolean; snapshotted?: boolean }>;
  list_file_history(relative_path: string): Promise<FileHistoryEntry[]>;
  read_file_history_entry(relative_path: string, entry_id: string): Promise<{ content: string; path: string; id: string }>;
  snapshot_file_history(relative_path: string, content: string): Promise<{ id: string; path: string }>;
  list_changesets?(
    conv_id?: string,
    group_id?: string,
    limit?: number,
    archived?: boolean,
  ): Promise<ChangesetRunDto[]>;
  get_changeset?(run_id: string): Promise<ChangesetRunDto>;
  get_changeset_entry_contents?(run_id: string, seq: number): Promise<{ path: string; before: string | null; after: string | null }>;
  revert_changeset_entry?(run_id: string, seq: number, force?: boolean, step?: boolean): Promise<ChangesetRevertResult>;
  revert_changeset?(run_id: string, force?: boolean, program?: string): Promise<ChangesetRevertResult>;
  export_changeset?(run_id: string): Promise<Record<string, unknown>>;
  archive_changesets?(
    conv_id?: string,
    group_id?: string,
    run_id?: string,
    archived?: boolean,
  ): Promise<{ updated: number }>;
  clear_changesets?(
    conv_id?: string,
    group_id?: string,
    run_id?: string,
    archived_only?: boolean,
  ): Promise<{ removed_runs: number; removed_blobs: number }>;
  /** Drop blocked/failed rows from a run. Applied writes cannot be deleted (they anchor revert). */
  delete_changeset_entries?(run_id: string, seqs: number[]): Promise<{ removed: number; kept: number }>;
  stop_verse_diagnostics_scan(project_root?: string): Promise<{ ok: boolean }>;
  report_open_tabs(window_id: string, tab_ids: string[]): Promise<void>;
  /** A plugin panel tab was shown, hidden or closed (agents are told when it is off screen). */
  report_plugin_panel?(window_id: string, tab_id: string, visible: boolean, open: boolean): Promise<void>;
  focus_tab(tab_id: string, requesting_window: string): Promise<{ ok: boolean; window_id: string }>;
  claim_tab(tab_id: string, window_id: string): Promise<void>;
  get_tab_owner(tab_id: string): Promise<string>;
  get_verse_lsp_status(client_id?: string): Promise<VerseLspStatusDto>;
  start_verse_lsp(project_root?: string, client_id?: string): Promise<VerseLspStatusDto>;
  stop_verse_lsp(client_id?: string): Promise<void>;
  load_verse_diagnostics_cache(project_root?: string): Promise<VerseDiagnosticsCacheDto>;
  save_verse_file_cache(
    relative_path: string,
    errors: number,
    warnings: number,
    items?: Array<{ line: number; column: number; message: string; severity: string }>,
    project_root?: string,
  ): Promise<{ ok: boolean; path?: string; error?: string }>;
  scan_verse_diagnostics(project_root?: string, full?: boolean): Promise<VerseDiagnosticsScanDto>;
  connect_verse_workflow(): Promise<VerseWorkflowStatusDto>;
  disconnect_verse_workflow(): Promise<VerseWorkflowStatusDto>;
  get_verse_workflow_status(): Promise<VerseWorkflowStatusDto>;
  compile_verse_project(): Promise<Record<string, unknown>>;
  push_verse_changes(verse_only?: boolean): Promise<Record<string, unknown>>;
  tester_list_devices(): Promise<TesterDevicesDto>;
  tester_simulate(device: string, event?: string, snapshot_json?: string): Promise<TesterSimulateDto>;
  tester_list_tests(): Promise<TesterListTestsDto>;
  tester_scaffold(overwrite?: boolean): Promise<{ ok: boolean; path?: string; created?: boolean; note?: string; error?: string }>;
  tester_run_tests(start_session?: boolean): Promise<TesterRunDto>;
  tester_results(last_n?: number, since_offset?: number): Promise<TesterResultsDto>;
  tester_create_chat(device_label?: string): Promise<{
    ok: boolean;
    conversation?: { id: string; title: string; ducky_style?: string };
    prompt?: string;
    device?: string;
    error?: string;
  }>;
  get_urc_status(project_root?: string): Promise<Record<string, unknown>>;
  urc_commit(message?: string, project_root?: string): Promise<Record<string, unknown>>;
  urc_push(project_root?: string): Promise<Record<string, unknown>>;
  is_verse_editor_enabled(): Promise<boolean>;
  open_project_file(relative_path: string): Promise<void>;
  open_asset_in_uefn?(
    relative_path: string,
  ): Promise<{ ok: boolean; error?: string; asset_path?: string; opened?: boolean | null }>;
  list_recent_projects(): Promise<RecentProject[]>;
  list_known_projects(rescan?: boolean): Promise<KnownProjectsListing>;
  /** Live @ / / and search rows plugins list (`contributes.chat.reference_sources`). */
  list_plugin_references?(): Promise<{
    refs?: Array<{ trigger: "@" | "/"; id: string; label: string; group: string; href: string; description?: string; icon_url?: string; plugin_id: string }>;
    /** Push types after which the menu asks the plugins again (`refresh_on`). */
    events?: string[];
  }>;
  inspect_project_folder(path: string): Promise<ProjectFolderInspection>;
  set_project_root(path: string): Promise<ProjectInfo>;
  delete_recent_project(path: string): Promise<ProjectInfo>;
  get_key_status(): Promise<Record<string, boolean>>;
  has_any_api_key(): Promise<boolean>;
  discord_list_bots(): Promise<{
    ok: boolean;
    bots?: DiscordBotDto[];
    error?: string;
    plugin_disabled?: boolean;
  }>;
  discord_save_bot(patch: {
    id?: string;
    bot_id?: string;
    create?: boolean;
    label?: string;
    guild_id?: string;
    post_as?: string;
    allowed_ids?: string;
    prefix?: string;
    enabled?: boolean;
    show_offline?: boolean;
    token?: string;
  }): Promise<{
    ok: boolean;
    bot?: DiscordBotDto;
    status?: DiscordStatusDto;
    error?: string;
  }>;
  discord_delete_bot(bot_id: string): Promise<{ ok: boolean; id?: string; error?: string }>;
  discord_status(bot_id?: string): Promise<DiscordStatusDto>;
  discord_list_channels(
    bot_id?: string,
  ): Promise<{ ok: boolean; bot_id?: string; channels?: DiscordChannelDto[]; error?: string }>;
  discord_debug(bot_id?: string): Promise<{
    bot_id?: string;
    watching_channel_id?: string;
    watching_channel_name?: string;
    poller_alive?: boolean;
    last_poll_age_s?: number | null;
    last_seen?: { author?: string; content_len?: number; preview?: string; ts?: string } | null;
  }>;
  discord_open_channel(
    channel_id: string,
    bot_id?: string,
  ): Promise<{
    ok: boolean;
    channel_id?: string;
    bot_id?: string;
    messages?: DiscordMessageDto[];
    error?: string;
  }>;
  discord_send(
    channel_id: string,
    text: string,
    bot_id?: string,
  ): Promise<{ ok: boolean; message?: DiscordMessageDto; bot_id?: string; error?: string }>;
  discord_list_members(bot_id?: string): Promise<{
    ok: boolean;
    guild_id?: string;
    bot_id?: string;
    groups?: DiscordMemberGroupDto[];
    ready?: boolean;
    error?: string;
  }>;
  discord_open_portal(bot_id?: string): Promise<{ ok: boolean; url?: string; bot_id?: string }>;
  save_agent_settings(
    patch: Partial<
      PanelSettingsDto & { keys?: Record<string, string>; discord_name?: string; discord_allowed_ids?: string }
    >,
  ): Promise<string>;
  /** Anonymous usage stats from the UI: a tab opened or a setup step (names outside that list are refused). */
  usage_track?(name: string, props?: Record<string, string | number | boolean>): Promise<boolean>;
  test_key(provider: string, key?: string): Promise<{ ok: boolean; detail: string }>;
  list_coding_agents(): Promise<{ agents: CodingAgentDto[]; checking?: boolean }>;
  set_conversation_coding_agent(
    conv_id: string,
    coding_agent: string,
    model?: string,
    provider?: string,
  ): Promise<{ ok: boolean; coding_agent?: string; model?: string; provider?: string; error?: string }>;
  /** A Ducky chat with no model takes the Default Model (agent and model), as a new chat does,
   * else Ducky AI. With neither, error says so and held counts waiting messages. */
  adopt_default_model?(
    conv_id: string,
    keep_agent?: boolean,
  ): Promise<{
    ok: boolean;
    coding_agent?: string;
    model?: string;
    provider?: string;
    held?: number;
    error?: string;
  }>;
  /** The composer picked a model: save it and run the messages that waited for one. */
  run_held_messages?(conv_id: string, model: string, coding_agent?: string): Promise<{ ok: boolean; ran?: number; error?: string }>;
  /** Messages this chat keeps until a model is picked. */
  held_for_model?(conv_id: string): Promise<{ ok: boolean; held: number }>;
  set_conversation_thinking_effort(
    conv_id: string,
    effort: string,
  ): Promise<{ ok: boolean; thinking_effort?: string; error?: string }>;
  detect_coding_agent_cli(agent_id: string): Promise<Record<string, unknown>>;
  coding_agent_login(
    agent_id: string,
  ): Promise<{
    ok: boolean;
    logged_in?: boolean;
    needs_login?: boolean;
    message?: string;
    error?: string;
    auth_url?: string;
    /** "code_modal": paste back the code the link shows. "device_code": type `user_code`
     *  at the link (any device). "browser": sign in at the link in a browser on the PC. */
    login_ui?: string;
    user_code?: string;
    terminal_session_id?: string;
  }>;
  coding_agent_login_submit(
    agent_id: string,
    code: string,
  ): Promise<{
    ok: boolean;
    logged_in?: boolean;
    message?: string;
    error?: string;
    auth_url?: string;
    restarted?: boolean;
  }>;
  coding_agent_login_status(
    agent_id: string,
  ): Promise<{
    ok: boolean;
    logged_in?: boolean;
    auth_url?: string;
    error?: string;
    login_ui?: string;
    user_code?: string;
    terminal_session_id?: string;
    session_alive?: boolean;
  }>;
  coding_agent_login_cancel(
    agent_id: string,
  ): Promise<{ ok: boolean; cancelled?: boolean; error?: string }>;
  coding_agent_logout(
    agent_id: string,
  ): Promise<{ ok: boolean; logged_in?: boolean; message?: string; error?: string }>;
  list_tasks(): Promise<{ tasks: Record<string, unknown>[] }>;
  create_task(title: string, goal?: string, conv_ids?: string[]): Promise<Record<string, unknown>>;
  add_task_phase(task_id: string, title: string, plan?: string): Promise<Record<string, unknown>>;
  write_task_artifact(task_id: string, name: string, content: string, kind?: string): Promise<Record<string, unknown>>;
  build_task_handoff(task_id: string, phase_id?: string): Promise<{ ok: boolean; prompt: string }>;
  verify_task(
    task_id: string,
    phase_id?: string,
    implementation_summary?: string,
  ): Promise<Record<string, unknown>>;
  get_plan(
    chat_id: string,
    project_root?: string | null,
  ): Promise<{
    ok: boolean;
    error?: string;
    plan: ChatPlan | null;
    progress: PlanProgress;
    outline?: PlanOutlineRow[];
  }>;
  list_plans(): Promise<{ ok: boolean; plans: PlanListItem[] }>;
  list_plan_templates?(): Promise<{ ok: boolean; templates: PlanTemplateListItem[] }>;
  get_plan_template?(
    template_id: string,
  ): Promise<{ ok: boolean; template?: ChatPlan; outline?: PlanOutlineRow[]; error?: string }>;
  create_plan_template?(
    title: string,
    overview?: string,
    body_markdown?: string,
    nodes?: PlanNode[],
  ): Promise<{ ok: boolean; template?: ChatPlan; outline?: PlanOutlineRow[]; error?: string }>;
  update_plan_template?(
    template_id: string,
    title?: string,
    overview?: string,
    body_markdown?: string,
    nodes?: PlanNode[],
  ): Promise<{ ok: boolean; template?: ChatPlan; outline?: PlanOutlineRow[]; error?: string }>;
  delete_plan_template?(
    template_id: string,
  ): Promise<{ ok: boolean; deleted?: boolean; error?: string }>;
  instantiate_plan_template?(
    template_id: string,
    chat_id: string,
    project_root?: string | null,
  ): Promise<{
    ok: boolean;
    plan?: ChatPlan;
    progress?: PlanProgress;
    outline?: PlanOutlineRow[];
    error?: string;
  }>;
  save_plan_as_template?(
    chat_id: string,
    project_root?: string | null,
  ): Promise<{ ok: boolean; template?: ChatPlan; error?: string }>;
  plan_add_node?(
    chat_id?: string,
    content?: string,
    parent_id?: string,
    index?: number | null,
    project_root?: string | null,
    template_id?: string,
    kind?: PlanNodeKind | string,
    body_markdown?: string,
  ): Promise<{
    ok: boolean;
    plan?: ChatPlan;
    progress?: PlanProgress | null;
    outline?: PlanOutlineRow[];
    error?: string;
  }>;
  plan_update_node?(
    chat_id?: string,
    node_id?: string,
    content?: string,
    status?: string,
    project_root?: string | null,
    template_id?: string,
    kind?: PlanNodeKind | string,
    body_markdown?: string,
  ): Promise<{
    ok: boolean;
    plan?: ChatPlan;
    progress?: PlanProgress | null;
    outline?: PlanOutlineRow[];
    error?: string;
  }>;
  plan_delete_node?(
    chat_id?: string,
    node_id?: string,
    project_root?: string | null,
    template_id?: string,
  ): Promise<{
    ok: boolean;
    plan?: ChatPlan;
    progress?: PlanProgress | null;
    outline?: PlanOutlineRow[];
    error?: string;
  }>;
  plan_move_node?(
    chat_id?: string,
    node_id?: string,
    parent_id?: string,
    index?: number,
    project_root?: string | null,
    template_id?: string,
  ): Promise<{
    ok: boolean;
    plan?: ChatPlan;
    progress?: PlanProgress | null;
    outline?: PlanOutlineRow[];
    error?: string;
  }>;
  list_memory_entries(
    project_root?: string | null,
  ): Promise<{ ok: boolean; entries: MemoryEntryMeta[]; dir: string }>;
  get_memory_entry(
    name: string,
    project_root?: string | null,
  ): Promise<{ ok: boolean; entry?: MemoryEntry; error?: string }>;
  save_memory_entry(
    name: string,
    content: string,
    description?: string,
    author?: string,
    project_root?: string | null,
  ): Promise<{ ok: boolean; entry?: MemoryEntryMeta; error?: string }>;
  delete_memory_entry(
    name: string,
    project_root?: string | null,
  ): Promise<{ ok: boolean; deleted?: boolean; error?: string }>;
  get_memory_settings(): Promise<MemorySettingsDto>;
  set_memory_settings(patch: Partial<MemorySettingsDto>): Promise<MemorySettingsDto>;
  get_chat_context_memory(
    conv_id: string,
    project_root?: string | null,
  ): Promise<ChatContextMemoryDto>;
  compress_chat_context(
    conv_id: string,
    project_root?: string | null,
    use_llm?: boolean,
  ): Promise<ChatContextMemoryDto>;
  clear_chat_context_summary(
    conv_id: string,
    project_root?: string | null,
  ): Promise<ChatContextMemoryDto>;
  delete_plan(
    chat_id: string,
    project_root?: string | null,
  ): Promise<{ ok: boolean; deleted?: boolean; error?: string }>;
  copy_plan(
    source_chat_id: string,
    dest_chat_id: string,
    source_project_root?: string | null,
    dest_project_root?: string | null,
  ): Promise<{ ok: boolean; plan?: ChatPlan; progress?: PlanProgress; error?: string }>;
  create_plan(
    chat_id: string,
    title: string,
    overview?: string,
    body_markdown?: string,
    todos?: Array<{ id?: string; content: string; status?: PlanTodoStatus }>,
    project_root?: string | null,
    nodes?: PlanNode[],
  ): Promise<{
    ok: boolean;
    plan?: ChatPlan;
    progress?: PlanProgress;
    outline?: PlanOutlineRow[];
    error?: string;
  }>;
  update_plan(
    chat_id: string,
    todos?: Array<{ id?: string; content: string; status?: PlanTodoStatus }>,
    title?: string,
    overview?: string,
    body_markdown?: string,
    merge?: boolean,
    status?: string,
    project_root?: string | null,
    nodes?: PlanNode[],
  ): Promise<{
    ok: boolean;
    plan?: ChatPlan;
    progress?: PlanProgress;
    outline?: PlanOutlineRow[];
    error?: string;
  }>;
  get_models(
    provider: string,
    refresh?: boolean,
  ): Promise<
    {
      id: string;
      name: string;
      provider: string;
      provider_key?: string;
      supports_vision?: boolean;
      supports_tools?: boolean;
      supports_web_search?: boolean;
      context_limit?: number;
      price_in?: number | null;
      price_out?: number | null;
      is_local?: boolean;
      supports_thinking_effort?: boolean | null;
      thinking_menu?: {
        lo?: string;
        hi?: string;
        levels: Array<{ id: string; label?: string; thinking_tokens?: number | null; hint?: string }>;
      } | null;
    }[]
  >;
  get_models_catalog?(refresh?: boolean): Promise<{
    models: Array<{
      id: string;
      name: string;
      provider: string;
      provider_key?: string;
      supports_vision?: boolean;
      supports_tools?: boolean;
      supports_web_search?: boolean;
      context_limit?: number;
      price_in?: number | null;
      price_out?: number | null;
      is_local?: boolean;
      supports_thinking_effort?: boolean | null;
      thinking_menu?: {
        lo?: string;
        hi?: string;
        levels: Array<{ id: string; label?: string; thinking_tokens?: number | null; hint?: string }>;
      } | null;
    }>;
    default_model?: string;
    agent_model?: string;
  }>;
  set_model(model_id: string, provider?: string): Promise<void>;
  send_message(
    conv_id: string,
    text: string,
    mode: AgentMode,
    model: string,
    file_path?: string,
    attachments?: MessageAttachmentDto[],
    coding_agent?: string,
  ): Promise<{ run_id: string }>;
  continue_interrupted(
    conv_id: string,
    mode: AgentMode,
    model: string,
    file_path?: string,
    coding_agent?: string,
  ): Promise<{ run_id: string }>;
  resend_last_user_message(
    conv_id: string,
    text: string,
    mode: AgentMode,
    model: string,
    file_path?: string,
    attachments?: MessageAttachmentDto[],
    coding_agent?: string,
  ): Promise<{ run_id: string }>;
  /** Restart from here: erase this user message (UI row id) and everything after it. */
  rewind_to_message?(conv_id: string, row_id: number, text: string): Promise<{ ok: boolean; kept?: number; error?: string }>;
  get_context_usage(
    conv_id: string,
    model: string,
    mode?: AgentMode,
    draft?: string,
    include_content?: boolean,
  ): Promise<ContextUsage>;
  get_gateway_usage?(
    provider: string,
    model?: string,
    refresh?: boolean,
  ): Promise<{
    windows: Array<{
      id: string;
      label: string;
      used: number;
      limit: number;
      unit?: string;
      reset?: string;
      readout?: string;
    }>;
    notice?: {
      message: string;
      action?: string;
      action_label?: string;
      provider_id?: string;
      agent_id?: string;
    };
    /** Account toggle under the bars; picking one calls ``plugin_call(plugin_id, rpc, {account})``,
     *  or core's ``set_gateway_account(provider, account)`` when ``plugin_id`` is "". */
    switch?: {
      plugin_id: string;
      rpc: string;
      provider?: string;
      value: string;
      options: Array<{ id: string; label: string }>;
    };
  }>;
  get_provider_usage(provider_id?: string, days?: number): Promise<ProviderUsageReport>;
  get_ducky_usage(ducky_name?: string, profile_id?: string, days?: number): Promise<DuckyUsageReport>;
  reset_context(conv_id: string, segments: string[], mode?: AgentMode, model?: string): Promise<ContextControlResult>;
  restore_context(conv_id: string, segments: string[], mode?: AgentMode, model?: string): Promise<ContextControlResult>;
  get_session_files(conv_id: string): Promise<SessionFile[]>;
  cancel_agent(conv_id?: string): Promise<boolean>;
  /** Send now while a turn runs: it takes the message at its next tool call. ok false = no turn runs. */
  steer_message?(conv_id: string, steer_id: string, text: string): Promise<{ ok?: boolean }>;
  /** Wait until the agent thread for this chat is gone (e.g. after Stop). */
  wait_for_agent_idle?(conv_id: string, timeout?: number): Promise<boolean>;
  report_ui_perf(entries: Array<Record<string, unknown>>): Promise<boolean>;
  /** An activity-list job only the UI runs ended: alerts when it failed or took long work. */
  notify_activity?(job: { title: string; detail?: string; phase: "done" | "error"; seconds: number }): Promise<boolean>;
  ui_rpc_respond(request_id: string, payload: Record<string, unknown>): Promise<boolean>;
  /** A question card answered after the asking agent's turn ended: delivered to that chat as a message. */
  answer_late_question?(conv_id: string, text: string): Promise<{ ok?: boolean; error?: string }>;
  ui_rpc_pending_questions?(): Promise<AgentEvent[]>;
  /** This window took a Show me / tour request meant for it. */
  ui_rpc_ack?(request_id: string): Promise<boolean>;
  /** A request sent to every window: true for the first window to claim it. */
  ui_rpc_claim?(request_id: string): Promise<boolean>;
  /** This window is the one in use (clicked / typed in), or it is closing. */
  ui_rpc_active?(client_id: string, active?: boolean): Promise<boolean>;
  list_running_agents(): Promise<string[]>;
  list_window_views?(): Promise<{ id: string; title: string; kind?: string }[]>;
  /** Launch UnrealEditorFortnite.exe with Epic hub extras — no island. */
  launch_uefn?(): Promise<{ ok: boolean; path?: string; exe?: string }>;
  /** Launch UnrealEditorFortnite.exe into the current island (-ValkyrieProject). */
  launch_uefn_project?(): Promise<{ ok: boolean; path?: string; exe?: string }>;
  /** WM_CLOSE UEFN, press Save, taskkill only if it is still up. */
  close_uefn?(): Promise<{ ok: boolean; killed?: boolean; graceful?: boolean }>;
  /** Close UEFN, then launch the current island again. */
  restart_uefn_project?(): Promise<{ ok: boolean; path?: string; killed?: boolean; graceful?: boolean }>;
  /** Restart the UEFN listener; when it is offline, turn Python on in UEFN through Epic MCP. */
  restart_listener?(): Promise<{ ok: boolean; action?: string; message?: string }>;
  rtc_signal?(session_id: string, payload: Record<string, unknown>): Promise<boolean>;
  /** Direct Remote View: the page posts its full-ICE answer for a viewer session. */
  direct_rtc_answer?(session: string, answer: { type: string; sdp: string } | null, fingerprint?: string, error?: string): Promise<boolean>;
  /** Direct Remote View: loopback signaling for local end-to-end runs. */
  direct_rtc_connect?(args: Record<string, unknown>): Promise<Record<string, unknown>>;
  /** Methods the desktop refuses over any remote transport. */
  remote_deny_methods?(): Promise<string[]>;
  /** Viewer connect telemetry (state, reason, connect_ms, candidate). */
  direct_rtc_report?(payload: Record<string, unknown>): Promise<void>;
  window_input?(hwnd: string | number, event: Record<string, unknown>): Promise<void>;
  window_box?(hwnd: string | number): Promise<WindowBox>;
  /** True when the watched window's thread is showing a text caret. */
  window_text_focus?(hwnd: string | number): Promise<boolean>;
  /** Blank our window titles (on) while getDisplayMedia lists windows; nested, restores itself after a minute. */
  capture_list_shield?(on: boolean): Promise<boolean>;
  pick_project_path(): Promise<string | null>;
  deploy(project_path?: string): Promise<string[]>;
  deploy_all_projects(): Promise<string[]>;
  apply_ide(kind: string): Promise<string>;
  test_ide(kind: string): Promise<{ ok: boolean; detail: string }>;
  get_ide_statuses(): Promise<Record<string, { ok: boolean; detail: string }>>;
  apply_all_ides(): Promise<string[]>;
  get_log(): Promise<string[]>;
  clear_log(): Promise<string[]>;
  get_errors(): Promise<string[]>;
  /** How many error and log lines there are, without reading them. */
  get_log_counts?(): Promise<{ errors: number; log: number }>;
  clear_errors(): Promise<string[]>;
  copy_support_dump(): Promise<string>;
  submit_feedback(payload: {
    message: string;
    email?: string;
    include_errors?: boolean;
    include_log?: boolean;
  }): Promise<{ ok: boolean; error?: string }>;
  copy_text(text: string): Promise<boolean>;
  pull_editor_log(): Promise<void>;
  open_appdata(): Promise<void>;
  appdata_overview(): Promise<AppDataOverview>;
  appdata_children(rel?: string): Promise<AppDataChildren>;
  appdata_open(rel?: string): Promise<void>;
  appdata_clear(rel: string): Promise<AppDataActionResult>;
  appdata_delete(rel: string): Promise<AppDataActionResult>;
  appdata_sweep(): Promise<Record<string, number>>;
  appdata_projects(): Promise<{ projects: AppDataProject[]; bytes: number }>;
  appdata_delete_project(slug: string): Promise<AppDataActionResult>;
  appdata_clear_caches(): Promise<AppDataActionResult>;
  store_overview(): Promise<StoreOverview>;
  store_table_preview(table: string, limit?: number, offset?: number): Promise<StoreTablePreview>;
  store_action(action: string, arg?: string): Promise<StoreActionResult>;
  search_status(): Promise<SearchStatus>;
  search_rebuild(): Promise<{ ok: boolean; error?: string }>;
  search_query(text: string, kind?: "skills" | "tools", limit?: number): Promise<SearchQueryResult>;
  open_path_in_explorer(path: string): Promise<void>;
  open_project_path_in_explorer(relative_path: string): Promise<void>;
  open_skills_folder(): Promise<void>;
  open_skill_packs_folder(): Promise<void>;
  open_skill_pack_folder(pack_id: string): Promise<void>;
  get_skill_info(): Promise<SkillInfoDto>;
  get_conversation_skills(conv_id: string): Promise<ConversationSkillsDto>;
  set_conversation_skills(conv_id: string, filenames: string[]): Promise<{ ok: boolean; enabled_skills: string[] }>;
  set_conversation_skill_selection(
    conv_id: string,
    enabled_packs: string[],
    enabled_subskills: Record<string, string[]>,
  ): Promise<ConversationSkillSelectionResultDto>;
  set_default_enabled_skills(filenames: string[]): Promise<{ ok: boolean; default_enabled_skills: string[] }>;
  set_default_skill_selection(
    enabled_packs: string[],
    enabled_subskills: Record<string, string[]>,
  ): Promise<ConversationSkillSelectionResultDto>;
  save_skill(text: string): Promise<SkillSaveResultDto>;
  save_skill_file(filename: string, text: string): Promise<SkillSaveResultDto>;
  save_subskill(pack_id: string, subskill_id: string, text: string): Promise<SkillSaveResultDto>;
  create_skill(filename: string, text?: string): Promise<SkillCreateResultDto>;
  create_skill_pack(pack_id: string, label: string, description?: string): Promise<SkillPackCreateResultDto>;
  draft_skill_pack(description: string, model: string, provider?: string): Promise<SkillPackDraftResultDto>;
  /** Run allowlisted PanelApi work on a worker; poll with bridge_job_poll. */
  bridge_job_start?(
    name: string,
    args?: unknown[],
  ): Promise<{ ok: boolean; job_id?: string; pending?: boolean; error?: string }>;
  bridge_job_poll?(
    job_id: string,
  ): Promise<Record<string, unknown> & { ok?: boolean; pending?: boolean; job_id?: string; error?: string }>;
  voice_transcribe_audio?(
    b64_audio: string,
    mime?: string,
  ): Promise<{ ok: boolean; text?: string; error?: string; model?: string }>;
  voice_create_realtime_token?(): Promise<{
    ok: boolean;
    value?: string;
    ws_url?: string;
    expires_at?: number;
    error?: string;
  }>;
  voice_summarize_reply?(
    assistant_text: string,
    model?: string,
  ): Promise<{ ok: boolean; text?: string; error?: string; verbatim?: boolean }>;
  /** Offline Windows Speech (desktop only). Jobs keep the bridge responsive. */
  voice_win_tts_start?(text: string, voice_id?: string): Promise<{ ok: boolean; job_id?: string; error?: string }>;
  voice_win_tts_voices_start?(): Promise<{ ok: boolean; job_id?: string; error?: string }>;
  voice_win_tts_poll?(job_id: string): Promise<{
    ok: boolean; pending?: boolean; error?: string; code?: string;
    audio_base64?: string; mime?: string;
    voices?: { id: string; label: string; lang?: string }[];
  }>;
  /** Windows dictation (desktop only — denied for remote). */
  voice_win_stt_prewarm?(): Promise<{ ok: boolean; error?: string; code?: string }>;
  voice_win_stt_start?(
    lang?: string,
  ): Promise<{ ok: boolean; session?: string; cursor?: number; lang?: string; error?: string; code?: string }>;
  voice_win_stt_poll?(
    session: string,
    cursor: number,
    wait_ms?: number,
  ): Promise<{ ok: boolean; events?: WinSttEvent[]; cursor?: number; active?: boolean }>;
  voice_win_stt_stop?(session: string): Promise<{ ok: boolean; error?: string }>;
  voice_win_stt_cancel?(session: string): Promise<{ ok: boolean; error?: string }>;
  voice_open_windows_settings?(page: string): Promise<{ ok: boolean; error?: string }>;
  plugin_tts_start?(
    plugin_id: string,
    text?: string,
    voice_id?: string,
  ): Promise<{ ok: boolean; job_id?: string; pending?: boolean; error?: string }>;
  plugin_tts_poll?(
    job_id: string,
  ): Promise<{
    ok: boolean;
    pending?: boolean;
    job_id?: string;
    audio_base64?: string;
    mime?: string;
    error?: string;
  }>;
  /** Fetch a plugin's dynamic voices (e.g. an account's cloned voices) — start then poll. */
  plugin_tts_voices_start?(
    plugin_id: string,
  ): Promise<{ ok: boolean; job_id?: string; pending?: boolean; error?: string }>;
  plugin_tts_voices_poll?(
    job_id: string,
  ): Promise<{
    ok: boolean;
    pending?: boolean;
    job_id?: string;
    voices?: Array<{ id: string; label?: string }>;
    error?: string;
  }>;
  /** Store an encrypted secret declared in a plugin's manifest secret_keys (e.g. an API key). */
  set_uefn_plugin_secret?(
    plugin_id: string,
    key: string,
    value: string,
  ): Promise<{ ok: boolean; plugin_id?: string; key?: string; set?: boolean; error?: string }>;
  /** Models whose chats may use a plugin's sensitive tools (`agent.tools.sensitive`). */
  get_uefn_plugin_allowed_models?(
    plugin_id: string,
  ): Promise<{ ok: boolean; plugin_id?: string; sensitive?: boolean; models?: string[]; error?: string }>;
  /** Save a plugin's Allowed models list (host Settings only; plugin panels cannot reach it). */
  set_uefn_plugin_allowed_models?(
    plugin_id: string,
    models: string[],
  ): Promise<{ ok: boolean; plugin_id?: string; models?: string[]; error?: string }>;
  /** Which of a plugin's secret keys are set (never returns the values). */
  get_uefn_plugin_secret_status?(
    plugin_id: string,
    keys?: string[],
  ): Promise<{ ok: boolean; plugin_id?: string; status?: Record<string, boolean>; error?: string }>;
  /** Test a plugin secret against the vendor API (draft value or saved key). */
  test_uefn_plugin_secret?(
    plugin_id: string,
    key: string,
    value?: string,
  ): Promise<{ ok: boolean; detail?: string }>;
  plugin_llm_start?(
    plugin_id: string,
    system?: string,
    user?: string,
    model?: string,
  ): Promise<{ ok: boolean; job_id?: string; pending?: boolean; error?: string }>;
  plugin_llm_poll?(
    job_id: string,
  ): Promise<{
    ok: boolean;
    pending?: boolean;
    cancelled?: boolean;
    job_id?: string;
    text?: string;
    error?: string;
    provider?: string;
    model?: string;
  }>;
  plugin_llm_cancel?(job_id: string): Promise<{ ok: boolean; cancelled?: boolean; error?: string }>;
  plugin_llm_complete?(
    plugin_id: string,
    system?: string,
    user?: string,
    model?: string,
  ): Promise<{ ok: boolean; text?: string; error?: string; provider?: string; model?: string }>;
  /** Same pipeline as MCP translate_ui_batch — start then poll. */
  plugin_translate_batch_start?(
    plugin_id: string,
    language: string,
    strings: Record<string, string> | string,
    model?: string,
  ): Promise<{ ok: boolean; job_id?: string; pending?: boolean; error?: string }>;
  plugin_translate_batch_poll?(
    job_id: string,
  ): Promise<{
    ok?: boolean;
    pending?: boolean;
    job_id?: string;
    language?: string;
    map?: Record<string, string>;
    missing?: string[];
    error?: string;
    provider?: string;
    model?: string;
  }>;
  plugin_cache_get?(
    plugin_id: string,
    key: string,
  ): Promise<{ ok: boolean; data?: Record<string, unknown>; error?: string }>;
  plugin_cache_set?(
    plugin_id: string,
    key: string,
    data?: Record<string, unknown>,
  ): Promise<{ ok: boolean; error?: string }>;
  plugin_cache_clear?(
    plugin_id: string,
    key?: string,
  ): Promise<{ ok: boolean; cleared?: string[]; error?: string }>;
  /** Host data service for panels: data.* / files.* / scope.get in the active scope. */
  plugin_data?(
    plugin_id: string,
    op: string,
    params?: Record<string, unknown>,
  ): Promise<Record<string, unknown> & { ok?: boolean; error?: string; code?: string }>;
  /** Where one plugin's data lives: Local or one shared space (picked per plugin). */
  plugin_scope_status?(plugin_id: string): Promise<PluginScopeStatus>;
  plugin_live_join?(plugin_id: string, room?: string): Promise<PluginLiveStatus & { ok?: boolean; error?: string }>;
  plugin_live_leave?(plugin_id: string): Promise<{ ok?: boolean }>;
  /** The title bar's Live menu: every team copy this PC has plugins on, and who is live where. */
  live_overview?(): Promise<LiveOverview>;
  /** Start live (no room) or Join a teammate's group on a team copy: every plugin of it goes in. */
  live_team_join?(account: string, space: string, room?: string): Promise<LiveTeam & { ok?: boolean; error?: string }>;
  live_team_leave?(account: string, space: string): Promise<{ ok?: boolean }>;
  /** Ask a teammate's PC into this PC's group (starts live first): their app asks them for 30 s. */
  live_team_invite?(account: string, space: string, device: string): Promise<{ ok?: boolean; error?: string; expires?: number }>;
  plugin_live_send?(plugin_id: string, topic: string, data: unknown, to?: string, fast?: boolean): Promise<{ ok?: boolean; error?: string; sent?: number }>;
  plugin_live_status?(plugin_id: string): Promise<PluginLiveStatus>;
  /** The page's live script presses (on) or lets go of a control; every teammate greys it out. */
  plugin_live_hold?(plugin_id: string, anchor: string, on: boolean, t: number, view: string): Promise<{ ok?: boolean; error?: string }>;
  /** What teammates hold in this plugin now. */
  plugin_live_holds?(plugin_id: string): Promise<{ ok?: boolean; holds?: PluginLiveHold[] }>;
  /** This PC's own live state in the plugin (an object up to 4 KB; null clears it). */
  plugin_live_set_my_state?(plugin_id: string, state: Record<string, unknown> | null): Promise<{ ok?: boolean; sent?: number; error?: string }>;
  /** The group's value of one of the plugin's components, through its leader (null drops it). */
  plugin_live_share?(plugin_id: string, key: string, value: unknown): Promise<{ ok?: boolean; error?: string }>;
  /** The group's shared values of the plugin now. */
  plugin_live_shared?(plugin_id: string): Promise<{ ok?: boolean; values?: Record<string, { value: unknown; by?: Record<string, unknown> }> }>;
  /** Every groupmate's live state in the plugin now. */
  plugin_live_member_states?(plugin_id: string): Promise<{ ok?: boolean; states?: { from: PluginLivePeer; state: Record<string, unknown> }[] }>;
  /** The main window answers the backend's rtc_cmd (backend/uefn_plugins/rtc.py). */
  rtc_reply?(request_id: string, result: Record<string, unknown>): Promise<boolean>;
  rtc_event?(peer: string, kind: string, payload: Record<string, unknown>): Promise<void>;
  /** Asks the spaces provider — only when a picker opens. */
  plugin_scope_choices?(): Promise<{ ok?: boolean; choices?: PluginScopeChoice[]; kindLabel?: string; emptyNote?: string; error?: string }>;
  /** Switches the plugin's copy; changes still queued for the shared space it leaves are
   * sent first (up to 30 s). `unsent`: how many of them didn't go (they stay in that copy).
   * A plugin with declared folders gets the team's files first (a failed pull changes
   * nothing); when the team has none yet the answer is `needs: "first_share"` with the
   * Local `units` to pick from, and the call again with `first_share` copies them (or
   * starts empty). `refused`: folder id → why it can't be shared from this PC. */
  plugin_scope_set?(
    plugin_id: string,
    scope_id: string,
    first_share?: PluginScopeFirstShare,
  ): Promise<PluginScopeStatus & {
    unsent?: number;
    unsentLabel?: string;
    needs?: "first_share";
    team?: string;
    note?: string;
    units?: PluginFolderShareUnit[];
    /** The team has some of the files already: `units` are the Local ones it lacks. */
    partial?: boolean;
    refused?: Record<string, string>;
  }>;
  /** The Team modal: one team copy ('' = the one most plugins show) and every plugin on it. */
  plugin_team_overview?(space?: string): Promise<TeamOverview>;
  /** Every plugin that can share shows Local or one team; each leaves its old live group at once.
   * `pending`: team copies that need a first share (left as they were). */
  plugin_scope_set_all?(scope_id: string): Promise<{
    ok?: boolean; switched?: string[]; error?: string;
    pending?: { plugin: string; team?: string; note?: string }[];
    errors?: { plugin: string; error: string }[];
  }>;
  /** An error inside a plugin panel (bridge `panel.error`), for ducky_plugin_errors. */
  plugin_panel_error?(plugin_id: string, panel_id: string, kind: string, message: string, stack: string): Promise<{ ok?: boolean }>;
  plugin_scope_sync?(plugin_id: string, force?: boolean): Promise<{ ok?: boolean; started?: boolean; error?: string }>;
  /** Send a refused or blocked change again; without `key`, every one of them. Refused while
   * the space's data is reset on the server (Upload or Clear my copy comes first). */
  plugin_scope_retry?(plugin_id: string, key?: string): Promise<{ ok?: boolean; error?: string }>;
  /** Drop this PC's refused or blocked change (the team's copy comes back on the next pull). */
  plugin_scope_drop?(plugin_id: string, kind: string, key: string): Promise<{ ok?: boolean; error?: string }>;
  /** A space whose data was reset on the server: Upload my copy or Clear my copy, for this
   * PC's whole copy of it (every plugin's data and its workflows). */
  space_reset_settle?(space_id: string, action: "upload" | "clear"): Promise<{ ok?: boolean; error?: string; queued?: number; cleared?: number }>;
  /** The drawer opened: the space is looked at again now (heads, presence; a round, so once
   * per open, never per status), and the status comes back with the drawer's tables. */
  plugin_scope_activity?(plugin_id: string): Promise<PluginScopeStatus>;
  /** Lock a unit by hand (kept until released). */
  plugin_scope_lock?(plugin_id: string, unit: string, name?: string): Promise<PluginScopeResult & { state?: string }>;
  plugin_scope_unlock?(plugin_id: string, unit: string): Promise<PluginScopeResult>;
  /** Break someone's lock (owners and admins, or anyone once its lease ran out). */
  plugin_scope_break?(plugin_id: string, unit: string): Promise<PluginScopeResult>;
  /** Ask the holder to release a unit. */
  plugin_scope_ask?(plugin_id: string, unit: string): Promise<PluginScopeResult>;
  /** Settle a conflict: mine, theirs, both (folder units: kind `tree`, key
   * `<folder>/<unit>`), or compare (returns `diff`, changes nothing). A folder unit changed
   * here while someone else holds it: keep_copy (Keep mine as a copy, beside it; `copy` is
   * where) or discard (Discard mine); the team's version comes back either way. */
  plugin_scope_resolve?(
    plugin_id: string,
    kind: string,
    key: string,
    choice: "mine" | "theirs" | "both" | "compare" | "keep_copy" | "discard",
  ): Promise<PluginScopeResult & { diff?: string; copy?: string }>;
  /** A new changeset on the server: items back to their version at a seq, the plugin as of
   * `toSeq`, or changeset `undoSeq` undone. `skipped`: items whose version is no longer kept. */
  plugin_scope_restore?(plugin_id: string, request: { items?: { kind: string; key: string; seq: number }[]; toSeq?: number; undoSeq?: number; message?: string }): Promise<PluginScopeResult & { seq?: number; skipped?: [string, string][] }>;
  /** Changesets newest first, or one item's versions (`kind` + `key`), before seq `before`. */
  plugin_scope_history?(plugin_id: string, request?: { kind?: string; key?: string; before?: number; limit?: number }): Promise<PluginScopeResult & { entries?: PluginScopeHistoryEntry[]; more?: boolean }>;
  plugin_scope_mode?(plugin_id: string, mode: "auto" | "manual"): Promise<PluginScopeResult & { mode?: string }>;
  /** Send this PC's changes now (Manual mode's Push), with an optional message. */
  plugin_scope_push?(plugin_id: string, message?: string): Promise<PluginScopeResult & { started?: boolean }>;
  /** Bring everyone's changes now (Pull, Update now); with `kind` + `key`, download that one item again. */
  plugin_scope_pull?(plugin_id: string, kind?: string, key?: string): Promise<PluginScopeResult & { started?: boolean }>;
  /** Lost access: copy this PC's unsent changes to the space into Local before it leaves this PC
   * (`save`), or let them go with it (`discard`). */
  plugin_scope_save_unsent?(plugin_id: string, action?: "save" | "discard"): Promise<PluginScopeResult & { saved?: { plugin: string; kind: string; key: string; localKey: string }[] }>;
  /** One way, on request: the plugin's Local data into one shared space's copy (its items are
   * kept). `tooBig`: keys left Local because they wouldn't fit once sealed. */
  plugin_data_copy_to_space?(plugin_id: string, space_id: string): Promise<{ ok?: boolean; copied?: number; kept?: number; tooBig?: string[]; error?: string }>;
  /** Opens the shared space's copy of this plugin's data on the web. */
  plugin_data_open_web?(plugin_id: string): Promise<{ ok?: boolean; url?: string; error?: string }>;
  plugin_prefs_get_all?(): Promise<{
    ok: boolean;
    prefs?: Record<string, Record<string, unknown>>;
    error?: string;
  }>;
  plugin_prefs_set_all?(
    prefs: Record<string, Record<string, unknown>>,
  ): Promise<{ ok: boolean; prefs?: Record<string, Record<string, unknown>>; error?: string }>;
  plugin_prefs_set?(
    plugin_id: string,
    prefs: Record<string, unknown>,
  ): Promise<{ ok: boolean; prefs?: Record<string, Record<string, unknown>>; error?: string }>;
  /** The chat's sandbox (programs, size, evidence pictures) for its context panel. */
  sandbox_status?(conv_id: string): Promise<SandboxStatus>;
  sandbox_stop_program?(conv_id: string, pid?: number): Promise<{ ok: boolean; error?: string }>;
  sandbox_delete?(conv_id: string): Promise<{ ok: boolean; error?: string }>;
  sandbox_apps?(): Promise<{ ok: boolean; apps?: SandboxApp[]; error?: string }>;
  sandbox_relaunch?(conv_id: string, name: string): Promise<{ ok: boolean; pid?: number; error?: string }>;
  sandbox_open_folder?(conv_id: string): Promise<{ ok: boolean; path?: string; error?: string }>;
  /** Dispatch a plugin panel RPC registered via ``api.register_panel_rpc``. */
  plugin_call?(
    plugin_id: string,
    method: string,
    params?: Record<string, unknown>,
  ): Promise<Record<string, unknown>>;
  draft_subskill(
    pack_id: string,
    label: string,
    description: string,
    model: string,
    provider?: string,
  ): Promise<SubskillDraftResultDto>;
  create_subskill(
    pack_id: string,
    subskill_id: string,
    label: string,
    description?: string,
    parent_id?: string,
    load_condition?: string,
  ): Promise<{ ok: boolean; path: string; subskill_id: string }>;
  delete_skill(filename: string): Promise<SkillDeleteResultDto>;
  delete_skill_pack(pack_id: string): Promise<SkillDeleteResultDto>;
  delete_subskill(pack_id: string, subskill_id: string): Promise<{ ok: boolean; error?: string }>;
  reset_skill(): Promise<SkillSaveResultDto>;
  reset_skill_pack(pack_id: string): Promise<SkillSaveResultDto>;
  get_skill_pack_graph(pack_id: string): Promise<SkillPackGraphDto>;
  get_skill_pack_files?(pack_id: string): Promise<SkillPackFilesDto>;
  save_skill_node(
    pack_id: string,
    node_id: string,
    patch: SkillNodePatchDto,
  ): Promise<{ ok: boolean; path?: string; node_id?: string; error?: string }>;
  save_pack_manifest?(
    pack_id: string,
    patch: SkillPackManifestPatchDto,
  ): Promise<{ ok: boolean; path?: string; pack_id?: string; error?: string }>;
  save_skill_layout(
    pack_id: string,
    layout: Record<string, { x: number; y: number }>,
  ): Promise<{ ok: boolean; path?: string; error?: string }>;
  create_skill_node(
    pack_id: string,
    subskill_id: string,
    label: string,
    description?: string,
    parent_id?: string,
    load_condition?: string,
  ): Promise<{ ok: boolean; path: string; subskill_id: string }>;
  export_skill_pack(pack_id: string): Promise<SkillPackExportResultDto>;
  prompt_save_skill_pack_export(
    filename: string,
    data_base64: string,
  ): Promise<SkillPackExportResultDto>;
  import_skill_pack(pack_id?: string, replace?: boolean): Promise<SkillPackImportResultDto>;
  import_skill_pack_bytes(
    filename: string,
    data_base64: string,
    pack_id?: string,
    replace?: boolean,
  ): Promise<SkillPackImportResultDto>;
  get_mcp_tools_catalog(): Promise<McpCatalogDto>;
  get_mcp_server_tools?(server_id: string): Promise<McpCatalogDto & { ok?: boolean; error?: string }>;
  get_mcp_diagnostics?(server_id: string, mode: "agent" | "ask" | "plan"): Promise<McpDiagnosticsDto>;
  /** Host tools a workflow Call tool node can run — no nested MCP connections. */
  get_workflow_tools_catalog?: () => Promise<McpCatalogDto>;
  list_mcp_plugins(): Promise<McpPluginListDto>;
  list_mcp_servers?: () => Promise<McpPluginListDto>;
  set_mcp_plugin_enabled(
    plugin_id: string,
    enabled: boolean,
  ): Promise<{
    ok: boolean;
    plugin_id?: string;
    enabled?: boolean;
    error?: string;
    needs_trust?: boolean;
    enabled_mcp_plugins?: string[];
    enabled_uefn_plugins?: string[];
    disabled_builtin_toolsets?: string[];
  }>;
  set_mcp_server_enabled?: (
    server_id: string,
    enabled: boolean,
  ) => Promise<{
    ok: boolean;
    plugin_id?: string;
    server_id?: string;
    enabled?: boolean;
    error?: string;
    needs_trust?: boolean;
  }>;
  test_mcp_plugin(plugin_id: string): Promise<McpPluginTestResultDto>;
  get_mcp_server_connection?(serverId: string): Promise<McpServerConnectionDto>;
  save_mcp_server_connection?(serverId: string, transport: string, url: string, command: string, args: string[], values: McpConnectionValueSave[]): Promise<McpServerConnectionDto>;
  test_mcp_server?: (server_id: string) => Promise<McpPluginTestResultDto>;
  create_mcp_plugin(
    plugin_id: string,
    label: string,
    description?: string,
    command?: string,
    args?: string[],
    env?: Record<string, string>,
    tool_prefix?: string,
    intents?: string[],
    transport?: string,
    url?: string,
    headers?: Record<string, string>,
  ): Promise<{ ok: boolean; path: string; plugin_id: string }>;
  create_mcp_server?: (
    server_id: string,
    label: string,
    description?: string,
    command?: string,
    args?: string[],
    env?: Record<string, string>,
    tool_prefix?: string,
    intents?: string[],
    transport?: string,
    url?: string,
    headers?: Record<string, string>,
  ) => Promise<{ ok: boolean; path: string; plugin_id: string; server_id?: string }>;
  delete_mcp_plugin(plugin_id: string): Promise<{ ok: boolean }>;
  delete_mcp_server?: (server_id: string) => Promise<{ ok: boolean }>;
  get_mcp_config?: () => Promise<{ ok: boolean; path?: string; text?: string; error?: string }>;
  set_mcp_config?: (text: string) => Promise<{ ok: boolean; path?: string; error?: string }>;
  open_mcp_plugins_folder(): Promise<void>;
  get_chat_mcp_plugins(
    conv_id: string,
  ): Promise<{ ok: boolean; error?: string; override?: string[] | null; plugins?: McpPluginDto[] }>;
  set_chat_mcp_plugins(
    conv_id: string,
    plugin_ids: string[] | null,
  ): Promise<{ ok: boolean; error?: string; override?: string[] | null }>;
  terminal_spawn(shell?: string, cwd?: string, title?: string): Promise<Record<string, unknown>>;
  terminal_kill(session_id: string): Promise<Record<string, unknown>>;
  terminal_busy(session_id: string): Promise<{ ok: boolean; busy?: boolean; running?: boolean; error?: string }>;
  terminal_busy_many(session_ids: string[]): Promise<{
    ok: boolean;
    states: Record<string, { ok: boolean; busy?: boolean; running?: boolean; error?: string }>;
  }>;
  terminal_list(): Promise<{ sessions: Record<string, unknown>[] }>;
  terminal_write(session_id: string, data: string): Promise<Record<string, unknown>>;
  terminal_resize(session_id: string, cols: number, rows: number): Promise<Record<string, unknown>>;
  terminal_request_command(
    session_id: string,
    command: string,
    source?: string,
    conv_id?: string,
  ): Promise<Record<string, unknown>>;
  /** scope: once, always (this command in the chat that asked) or all (everything in it). */
  terminal_approve_command(
    request_id: string,
    scope?: "once" | "always" | "all",
    answered_in?: string,
  ): Promise<Record<string, unknown>>;
  /** Agent commands still waiting for an answer (cards to show again after a reload). */
  terminal_pending_commands?(): Promise<Array<Record<string, unknown>>>;
  terminal_reject_command(request_id: string, reason?: string): Promise<Record<string, unknown>>;
  terminal_read_output(session_id: string, max_chars?: number): Promise<Record<string, unknown>>;
  exit_all(): Promise<boolean>;
}

export interface SkillFileDto {
  filename: string;
  path: string;
  kind: "primary" | "custom";
  text: string;
  bundled_text?: string;
  is_custom: boolean;
}

export interface SubskillDto {
  id: string;
  label: string;
  description: string;
  default_enabled: boolean;
  always_on?: boolean;
  parent_id?: string | null;
  load_condition?: string | null;
  file?: string;
  text?: string;
  /** ``user`` / ``store`` / ``shipped`` / ``plugin``. */
  origin?: string;
}

export interface SkillPackDto {
  id: string;
  label: string;
  description: string;
  kind: "bundled" | "custom" | "plugin" | "store" | string;
  path: string;
  version: number | string;
  license?: string;
  author?: string;
  copyright?: string;
  homepage?: string;
  contact?: string;
  allow_redistribute?: boolean;
  /** Desktop plugin id when this pack ships inside a plugin. */
  source_plugin_id?: string;
  /** ``store`` when installed from the Store catalog. */
  source?: string;
  /** Store catalog slug for deep-linking to the listing. */
  store_slug?: string;
  /** Pack-level origin (``user`` for studio-created). */
  origin?: string;
  layout?: Record<string, { x: number; y: number }>;
  subskills: SubskillDto[];
}

export interface SkillPackManifestPatchDto {
  label?: string;
  description?: string;
  license?: string | null;
  author?: string | null;
  copyright?: string | null;
  homepage?: string | null;
  contact?: string | null;
  allow_redistribute?: boolean;
}

export interface SkillGraphNodeDto {
  id: string;
  parent_id: string | null;
  type: "skill";
  label: string;
  description: string;
  default_enabled: boolean;
  always_on: boolean;
  load_condition: string | null;
  file: string;
  text: string;
  x: number;
  y: number;
}

export interface SkillPackGraphDto {
  ok: boolean;
  pack_id: string;
  label: string;
  description: string;
  kind: string;
  version: number | string;
  layout: Record<string, { x: number; y: number }>;
  nodes: SkillGraphNodeDto[];
  error?: string;
}

export interface SkillNodePatchDto {
  label?: string;
  description?: string;
  default_enabled?: boolean;
  load_condition?: string | null;
  parent_id?: string | null;
}

export interface SkillPackFileDto {
  id: string;
  file: string;
  label: string;
  description: string;
  default_enabled: boolean;
  always_on: boolean;
  load_condition: string | null;
  text: string;
}

export interface SkillPackFilesDto {
  ok: boolean;
  pack_id: string;
  label: string;
  description: string;
  kind: string;
  version: number | string;
  path?: string;
  files: SkillPackFileDto[];
  error?: string;
}

export interface SkillPackExportResultDto {
  ok: boolean;
  path?: string;
  pack_id?: string;
  filename?: string;
  data_base64?: string;
  cancelled?: boolean;
  error?: string;
}

export interface SkillPackImportResultDto {
  ok: boolean;
  pack_id?: string;
  path?: string;
  label?: string;
  license?: string;
  author?: string;
  allow_redistribute?: boolean;
  conflict?: boolean;
  existing_id?: string;
  cancelled?: boolean;
  error?: string;
}

export interface SkillCatalogEntryDto {
  id?: string;
  filename: string;
  kind: "primary" | "custom" | "bundled" | string;
  label: string;
  description: string;
  subskills?: SubskillDto[];
}

export interface ConversationSkillsDto {
  disabled_packs?: string[];
  enabled_packs: string[];
  enabled_subskills: Record<string, string[]>;
  enabled_skills: string[];
  packs: SkillPackDto[];
  catalog: SkillCatalogEntryDto[];
  primary_filename: string;
}

export interface AgentProfileDto {
  id: string;
  name: string;
  ducky_style: string;
  ducky_personality: string;
  when_to_use?: string;
  /** Pack ids this ducky cannot use. Empty = all packs available (lazy-loaded). */
  disabled_packs: string[];
  /** Per-pack toggleable subskill allowlist (omit pack = all references available). */
  enabled_subskills?: Record<string, string[]>;
  /** Tool/MCP group ids this ducky cannot use. Empty = all available. */
  disabled_tool_ids: string[];
  favorite_models?: string[];
  tts_voice?: string;
  tts_speed?: number;
  kind?: "bundled" | "custom" | string;
}

export interface AgentProfileListDto {
  profiles: AgentProfileDto[];
  archived_profiles?: AgentProfileDto[];
  template_profiles?: AgentProfileDto[];
  blank_profile_id: string;
}

export interface DuckyConfigDto {
  title?: string;
  ducky_name?: string;
  /** Library agent-profile id this chat was created from (stable across renames). */
  profile_id?: string;
  ducky_style?: string;
  ducky_personality?: string;
  disabled_packs?: string[];
  enabled_subskills?: Record<string, string[]>;
  disabled_tool_ids?: string[];
  favorite_models?: string[];
  tts_voice?: string;
  tts_speed?: number;
  /** Anthropic extended-thinking effort for new/edited chats. */
  thinking_effort?: string;
  /** External coding agent when the selected model is Claude Code / Codex / Cursor. */
  coding_agent?: string;
  /** Create the ducky on this island instead of the one that is open. */
  project_slug?: string;
}

export interface AgentProfileEditorCatalogDto {
  packs: SkillPackDto[];
  default_disabled_packs: string[];
  default_disabled_tool_ids: string[];
  /** Derived: all packs minus default_disabled_packs (compat for older UI). */
  default_enabled_packs?: string[];
  default_enabled_subskills?: Record<string, string[]>;
  tools: McpPluginDto[];
  /** All globally available tool-group ids (compat). */
  default_tool_ids?: string[];
  /** True while UEFN Store plugins are still registering — tools list is partial. */
  plugins_loading?: boolean;
}

export interface ConversationSkillSelectionResultDto {
  ok: boolean;
  enabled_packs: string[];
  enabled_subskills: Record<string, string[]>;
  default_enabled_packs?: string[];
  default_enabled_subskills?: Record<string, string[]>;
  default_enabled_skills?: string[];
  enabled_skills?: string[];
}

export interface SkillInfoDto {
  text: string;
  primary_text: string;
  active_source: string;
  version: number;
  path: string;
  appdata_dir: string;
  skills_dir: string;
  skill_packs_dir?: string;
  appdata_exists: boolean;
  appdata_text: string;
  appdata_version: number;
  bundled_path: string;
  bundled_text: string;
  bundled_version: number;
  is_custom: boolean;
  files: SkillFileDto[];
  packs?: SkillPackDto[];
  primary_filename: string;
  catalog?: SkillCatalogEntryDto[];
  default_enabled_packs?: string[];
  default_enabled_subskills?: Record<string, string[]>;
  default_enabled_skills?: string[];
  error: string;
}

export interface SkillSaveResultDto {
  ok: boolean;
  path: string;
  version: number;
  filename?: string;
  logs: string[];
}

export interface SkillPackCreateResultDto {
  ok: boolean;
  path: string;
  pack_id: string;
}

export interface SkillPackDraftFileDto {
  id: string;
  label: string;
  description: string;
  markdown: string;
}

export interface SkillPackDraftDto {
  label: string;
  description: string;
  core_markdown: string;
  files: SkillPackDraftFileDto[];
}

export interface SkillPackDraftResultDto {
  ok: boolean;
  draft?: SkillPackDraftDto;
  error?: string;
}

export interface SubskillDraftFileDto {
  id: string;
  label: string;
  description: string;
  markdown: string;
}

export interface SubskillDraftResultDto {
  ok: boolean;
  draft?: SubskillDraftFileDto;
  error?: string;
}

export interface SkillCreateResultDto {
  ok: boolean;
  path: string;
  filename: string;
  pack_id?: string;
}

export interface SkillDeleteResultDto {
  ok: boolean;
  filename: string;
  error?: string;
}

export interface McpToolParameterDto {
  name: string;
  type: string;
  description: string;
  required: boolean;
  default?: unknown;
}

export interface McpToolDto {
  name: string;
  description: string;
  category_id: string;
  category_label: string;
  in_agent: boolean;
  in_plan: boolean;
  agent_excluded: boolean;
  destructive: boolean;
  host_only: boolean;
  is_plugin?: boolean;
  parameters: McpToolParameterDto[];
}

export interface McpPluginDto {
  id: string;
  label: string;
  description: string;
  version: number;
  kind: string;
  transport: string;
  tags: string[];
  enabled: boolean;
  default_enabled: boolean;
  requirements_note: string;
  setup_steps: string[];
  tool_prefix: string;
  /** Tool names for kind === "uefn_plugin" (desktop plugin MCP hook). */
  tool_names?: string[];
  intents: string[];
  path: string;
  /** HTTP/SSE URL from mcp.json (empty for stdio). */
  url?: string;
  /** Normalized host:port for HTTP/SSE servers. */
  http_bind?: string;
  /** True when this enabled server shares a TCP port with another enabled HTTP/SSE MCP. */
  port_conflict?: boolean;
  /** Peer server ids on the same host:port. */
  port_conflict_with?: string[];
  /** True when enable is blocked because another enabled server owns this port. */
  enable_blocked_by_port?: boolean;
}

export interface McpPluginListDto {
  ok: boolean;
  plugins: McpPluginDto[];
}

export interface McpPluginTestResultDto {
  ok: boolean;
  error?: string;
  hint?: string;
  tool_count?: number;
  stages?: { stage: string; ok: boolean; error?: string; tool_count?: number; tool?: string }[];
  tools?: string[];
}

/** One header (HTTP / SSE) or env var (stdio). The value itself never comes to the UI. */
export interface McpConnectionValueDto {
  name: string;
  /** The saved secret it reads (${SECRET:NAME}), if any. */
  secret: string;
  has_value: boolean;
  /** Enough to recognise it: "Bearer ••••9f2c". */
  masked: string;
}

export interface McpServerConnectionDto {
  ok: boolean;
  error?: string;
  server_id?: string;
  kind?: string;
  /** Catalog servers only take new key values; custom ones take everything. */
  editable?: boolean;
  transport?: string;
  url?: string;
  command?: string;
  args?: string[];
  values?: McpConnectionValueDto[];
  values_label?: string;
}

/** A row the editor saves: a typed value replaces it; keep holds the stored one. */
export interface McpConnectionValueSave {
  name: string;
  from?: string;
  value?: string;
  keep?: boolean;
}

export interface McpCategoryDto {
  id: string;
  label: string;
  tools: McpToolDto[];
}

export interface McpDiagnosticsDto {
  correlation_id: string;
  stage_counts: { catalog_tools: number | null; started_servers: number | null; connected_servers: number | null; model_exposed_tools: number | null };
  mode: "agent" | "ask" | "plan";
  scope: string;
  stage_note: string;
  policy_guidance: string;
  observation_error: "observation_unavailable" | null;
  servers: Array<{
    server_id: string;
    enabled: boolean | null;
    status: "missing" | "disabled" | "offline" | "connected" | "unknown";
    stages: { started: boolean | null; connected: boolean | null; model_exposed: boolean | null };
    counts: { catalog: number | null; policy_blocked: number | null; unexposed_by_filter: number | null };
    recent_error: "inventory_refresh_failed" | "http_connection_failed" | "unobserved";
    guidance: string;
    tools: Array<{ name: string; status: "unexposed" | "policy-blocked" | "unknown"; mode_reason: string }>;
    tools_truncated: boolean;
  }>;
}

export interface McpCatalogDto {
  total: number;
  agent_tools: number;
  plan_tools: number;
  categories: McpCategoryDto[];
  tools: McpToolDto[];
}

export interface VerseLspWorkspaceFolderDto {
  name: string;
  path: string;
}

export interface VerseLspStatusDto {
  available: boolean;
  lsp_path: string;
  source: string;
  running: boolean;
  ws_url: string;
  project_root: string;
  error: string;
  enabled?: boolean;
  stderr_tail?: string[];
  proc_alive?: boolean;
  bridge_up?: boolean;
  last_exit_code?: number | null;
  workspace_folders?: VerseLspWorkspaceFolderDto[];
  watch_files?: string[];
  /** Ducky-owned .vproject handed to verse-lsp when the island's built-in digests are missing; "" when the real file is used. */
  vproject_shadow?: string;
}

export interface VerseDiagnosticsScanDto {
  files: Array<{ path: string; errors: number; warnings: number; items?: unknown[] }>;
  scanned: number;
  with_errors?: number;
  with_warnings?: number;
  error?: string;
  ok?: boolean;
  pending?: boolean;
  bridge_scan?: boolean;
  paths?: string[];
}

export interface VerseDiagnosticsCacheDto {
  files: Array<{ path: string; errors: number; warnings: number; items?: unknown[] }>;
  stale_count: number;
  from_cache: boolean;
}

export interface TesterDeviceNode {
  id: string;
  label: string;
  class?: string;
  kind?: string;
  path?: string;
  verse_source?: string;
  placed?: boolean;
}

export interface TesterDevicesDto {
  ok: boolean;
  uefn_online?: boolean;
  epic_mcp_online?: boolean;
  listener_online?: boolean;
  live_source?: "epic" | "listener" | null;
  live?: { nodes?: TesterDeviceNode[]; edges?: unknown[]; count?: number } | null;
  workspace?: { nodes?: TesterDeviceNode[]; count?: number };
  audit?: {
    issues?: Array<{ severity?: string; code?: string; count?: number; detail?: string }>;
    unwired?: Array<{ device?: string; field?: string }>;
    orphans?: Array<{ label?: string; id?: string }>;
    summary?: { errors?: number; warnings?: number };
  } | null;
  error?: string | null;
}

export interface TesterSimulateDto {
  ok: boolean;
  device?: string;
  event?: string;
  trace?: Array<{
    depth?: number;
    device?: string;
    incoming?: string;
    emits?: string[];
    effects?: Array<{ kind?: string; detail?: string }>;
    skipped?: boolean;
    reason?: string;
  }>;
  effects?: Array<{ device?: string; kind?: string; detail?: string }>;
  error?: string;
  note?: string;
  listener_online?: boolean;
  uefn_online?: boolean;
}

export interface TesterListTestsDto {
  ok: boolean;
  tests?: Array<{
    id: string;
    name: string;
    kind: string;
    path?: string;
    device?: string;
    event?: string;
    cases?: string[];
  }>;
  count?: number;
  error?: string;
}

export interface TesterRunDto {
  ok?: boolean;
  compile?: Record<string, unknown>;
  push?: Record<string, unknown>;
  session?: { started?: boolean; error?: string };
  error?: string;
}

export interface TesterResultsDto {
  ok?: boolean;
  passed?: number;
  failed?: number;
  skipped?: number;
  total?: number;
  results?: Array<{ status: string; name: string; detail?: string; raw?: string }>;
  log_offset?: number;
  log_error?: string;
  error?: string;
}

export interface VerseWorkflowStatusDto {
  connected: boolean;
  build_state: number;
  can_push_verse_changes: boolean;
  last_log: string;
  error?: string;
}

declare global {
  interface Window {
    pywebview?: { api: PanelApi };
    __uefnPushEvent?: (event: AgentEvent) => void;
    __uefnPanelPush?: (event: PanelPushEvent) => void;
    __uefnFocusTabOpen?: (event: { focus_id: string; title: string }) => void;
    __uefnFocusTabActivate?: (event: { focus_id: string; title: string }) => void;
    __uefnFocusTabClose?: (event: { focus_id: string; title: string }) => void;
    __uefnFocusTabReturn?: (event: { focus_id: string; title: string }) => void;
    __uefnFocusTabActive?: (event: { focus_id: string; title: string }) => void;
    __uefnFocusRestoreLayout?: (event: { layout: EditorLayoutState }) => void;
  }
}

export {};
