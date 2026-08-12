export type AgentMode = "ask" | "plan" | "agent";

export type ViewId = "chat" | "settings";

export type ChatLayoutMode = "full" | "sidebarHidden";

export function nextChatLayoutMode(mode: ChatLayoutMode): ChatLayoutMode {
  return mode === "full" ? "sidebarHidden" : "full";
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
  download_url: string;
  /** none | no_release | up_to_date | update_available | error */
  feed_status?: string;
  error: string | null;
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

export interface GroupMemberDto {
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
}

export interface MessageAuthorDto {
  name: string;
  member_conv_id?: string;
  tts_voice?: string;
  tts_speed?: number;
  color?: string;
  profile_id?: string;
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
  | "ducky-profile";

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
}

export interface FolderDto {
  id: string;
  name: string;
  parent_id: string;
  sort_order: number;
  group_hub_id?: string;
}

export interface SidebarChatLayout {
  id: string;
  folder_id: string;
  sort_order: number;
}

export interface SidebarLayoutPatch {
  folders: Array<{ id: string; parent_id: string; sort_order: number }>;
  chats: SidebarChatLayout[];
}

export interface ToolCallData {
  name: string;
  arguments: Record<string, unknown>;
  status?: "pending" | "success" | "error" | "cancelled" | string;
  durationMs?: number;
  result?: string;
  llmTokens?: number;
  hint?: string;
  fileEdit?: FileEditData;
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
}

export interface MessageAttachmentDto {
  kind: "image" | "file";
  name: string;
  mime?: string;
  data_base64?: string;
  text?: string;
  /** Project Saved/DuckyCaptures path when the snip was mirrored there. */
  project_path?: string;
}

export type ComposerAttachment =
  | { id: string; kind: "image"; name: string; mime: string; dataUrl: string; projectPath?: string }
  | { id: string; kind: "file"; name: string; mime: string; text: string };

export interface ListenerStatus {
  online: boolean;
  wedged?: boolean;
  version: string;
  uptime_sec?: number;
  status_text?: string;
  uefn_project_dir?: string;
  uefn_project_name?: string;
  project_match?: boolean;
}

export interface ProjectInfo {
  path: string;
  name: string;
  slug: string;
}

export interface ProjectFileEntry {
  name: string;
  path: string;
  is_dir: boolean;
  read_only?: boolean;
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

export interface WorkspaceSearchResult {
  query: string;
  scope: WorkspaceSearchScope;
  file_results: WorkspaceFileSearchResult[];
  chat_results: WorkspaceChatSearchResult[];
  stats: {
    file_count: number;
    file_match_count: number;
    chat_count: number;
    chat_match_count: number;
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
  /** "agent" when written by an AI tool; empty/omitted for user saves. */
  source?: string;
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
  uefn_project_root: string;
  antigravity_config_path: string;
  verse_diagnostics_cache_enabled?: boolean;
  verse_diagnostics_auto_check?: boolean;
  show_hidden_project_files?: boolean;
  terminals_enabled?: boolean;
  prompt_caching_enabled?: boolean;
  freeze_prompt_prefix?: boolean;
  anthropic_extended_cache_ttl?: boolean;
  default_coding_agent?: string;
  duckyos_base_url?: string;
  voice_enabled?: boolean;
  voice_spoken_style?: string;
  voice_summary_model?: string;
  voice_default_voice?: string;
  voice_default_speed?: number;
  voice_live_manual_send?: boolean;
  /** 0–1: how much live voice narrates tools/thinking. */
  voice_process_talk?: number;
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
  /** Cheap model that refines the auto role title (empty = keyword names only). */
  chat_title_model?: string;
  /** Follow agent file edits in the editor (walkthrough). */
  follow_code_enabled?: boolean;
  follow_code_speed?: "slow" | "normal" | "fast" | "instant" | string;
  follow_code_split_beside_chat?: boolean;
  /** Product walkthrough completion flags keyed by tour id. */
  walkthrough_completed?: Record<string, boolean>;
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

export interface DuckyOSAccountStatus {
  ok?: boolean;
  logged_in?: boolean;
  needs_code?: boolean;
  base_url?: string;
  default_base_url?: string;
  email?: string;
  display_name?: string;
  user_id?: string;
  roles?: string[];
  permissions?: string[];
  device_key_active?: boolean;
  device_key_error?: string;
  session_expired?: boolean;
  error?: string;
  code?: string;
  email_hint?: string;
  ttl_seconds?: number;
}

export interface DuckyOSTeamMemberDto {
  user_id?: string;
  email?: string;
  role?: string;
  display_name?: string;
  online?: boolean;
  presence?: {
    source?: string;
    project_label?: string;
    uefn_online?: boolean;
    last_seen?: string;
  };
}

export interface DuckyOSTeamDto {
  id?: string;
  name?: string;
  slug?: string;
  description?: string;
  my_role?: string;
  members?: DuckyOSTeamMemberDto[];
}

export interface DuckyOSTeamsSnapshot {
  ok?: boolean;
  error?: string;
  code?: string;
  teams?: DuckyOSTeamDto[];
  needs_team?: boolean;
  online?: Array<{
    user_id?: string;
    display_name?: string;
    source?: string;
    project_label?: string;
    uefn_online?: boolean;
    is_self?: boolean;
  }>;
  teams_url?: string;
  invite_url?: string;
  stale_seconds?: number;
}

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
  price_cents?: number;
  currency?: string;
  paid?: boolean;
  owned?: boolean | null;
  stripe_product_key?: string | null;
  /** Content summary when a plugin bundles skills/themes/etc. */
  contributes_summary?: string[];
}

export interface DuckyOSStoreCatalog {
  ok?: boolean;
  error?: string;
  code?: string;
  items?: DuckyOSStoreItemDto[];
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
  capabilities?: {
    terminal_agent?: boolean;
    chat_api?: boolean;
    a2a?: boolean;
    mcp_inject?: boolean;
    needs_api_key?: boolean;
    needs_cli?: boolean;
  };
  models?: { id: string; name: string; provider?: string }[];
}

export type LinkedAgentStatus = "running" | "done" | "error" | "timeout" | "cancelled";

export interface LinkedAgent {
  childConvId: string;
  title: string;
  status: LinkedAgentStatus;
}

export interface AgentEvent {
  type:
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
    | "tab_claimed"
    | "tab_focus_request"
    | "file_renamed"
    | "file_deleted"
    | "delegation_warning"
    | "plan_updated"
    | "discord_message"
    | "ui_rpc_request"
    | "settings_changed"
    | "mcp_plugins_changed"
    | "skills_changed"
    | "duckies_changed";
  text?: string;
  /** ui_rpc_request: which panel method to run and its params. */
  method?: string;
  params?: Record<string, unknown>;
  success?: boolean;
  conv_id?: string;
  run_id?: string;
  /** On an error event: true when the backend kept the partial reasoning/answer. */
  kept_partial?: boolean;
  reason?: "cancelled" | "done" | "error" | string;
  parent_conv_id?: string;
  child_conv_id?: string;
  title?: string;
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
  request_id?: string;
  command?: string;
  source?: string;
  channel_id?: string;
  /** Multi-bot Discord: which bot profile emitted this event. */
  bot_id?: string;
  discord?: DiscordMessageDto;
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
    | "uefn_plugin_trust_request"
    | "browser_pane_state"
    | "browser_pane_new_window";
  provider?: string;
  ok?: boolean;
  detail?: string;
  appearance?: AppearanceDto;
  project?: ProjectInfo;
  /** uefn_plugin_trust_request — AI/local plugin awaiting user confirm. */
  plugin_id?: string;
  source?: string;
  /** browser_pane_state fields (native WebView2 pane navigation state). */
  pane_id?: string;
  url?: string;
  title?: string;
  can_back?: boolean;
  can_forward?: boolean;
  loading?: boolean;
  ready?: boolean;
  failed?: string;
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

export interface PanelApi {
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
  adopt_tab_into_this_focus_window(focus_id: string, title: string): Promise<void>;
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
  return_tab_to_main(focus_id: string, title: string): Promise<boolean>;
  close_focus_window(focus_id: string): Promise<void>;
  close_all_focus_windows(): Promise<void>;
  get_editor_workspace(slug?: string): Promise<EditorWorkspaceSnapshot>;
  save_editor_workspace(payload: EditorWorkspaceSnapshot): Promise<void>;
  /** Durable left/right dock rails (AppData). Empty object = nothing saved yet. */
  get_workspace_dock?(window_id?: string): Promise<Record<string, unknown>>;
  save_workspace_dock?(payload: {
    window_id?: string;
    snapshot: Record<string, unknown>;
  }): Promise<void>;
  restore_focus_windows(groups?: FocusWindowSnapshot[]): Promise<void>;
  report_editor_state(relative_path: string, state: Record<string, unknown>): Promise<void>;
  close_this_window(): Promise<void>;
  is_focus_window(): Promise<boolean>;
  toggle_maximize(): Promise<boolean>;
  is_window_maximized?(): Promise<boolean>;
  minimize_window(): Promise<void>;
  get_version(): Promise<string>;
  get_app_update_status(): Promise<AppUpdateStatus>;
  get_install_info(): Promise<InstallInfo>;
  apply_update(): Promise<UpdaterResult>;
  get_update_progress(): Promise<UpdateProgress>;
  cancel_update(): Promise<{ ok: boolean }>;
  launch_uninstall(): Promise<UpdaterResult>;
  open_download_page(): Promise<void>;
  open_patreon_page(): Promise<void>;
  open_external_url(url: string): Promise<void>;
  /** Open WebView2 DevTools / Inspector (production-safe). */
  open_devtools?(): Promise<{ ok: boolean; error?: string }>;
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
  duckyos_login(base_url?: string): Promise<DuckyOSAccountStatus>;
  duckyos_cancel_login(): Promise<DuckyOSAccountStatus>;
  /** @deprecated Verification happens in the browser. */
  duckyos_submit_code(code?: string): Promise<DuckyOSAccountStatus>;
  duckyos_logout(): Promise<DuckyOSAccountStatus>;
  duckyos_open_admin(): Promise<void>;
  duckyos_teams_snapshot?(stale_seconds?: number): Promise<DuckyOSTeamsSnapshot>;
  duckyos_open_teams_site?(path?: string): Promise<{ ok?: boolean; url?: string; error?: string }>;
  duckyos_store_catalog?(): Promise<DuckyOSStoreCatalog>;
  duckyos_store_download?(
    slug: string,
    version?: string,
    isUpdate?: boolean,
  ): Promise<DuckyOSStoreInstallResult>;
  duckyos_store_checkout?(slug: string): Promise<{ ok?: boolean; error?: string; code?: string; url?: string; slug?: string }>;
  duckyos_store_grant?(sessionId: string, slug?: string): Promise<{ ok?: boolean; error?: string; code?: string; slug?: string; alreadyOwned?: boolean }>;
  list_uefn_plugins?(): Promise<{ ok?: boolean; error?: string; plugins?: UefnPluginDto[] }>;
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
  burst_desktop_confetti(client_x: number, client_y: number): Promise<void>;
  snip_screen?(): Promise<{
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
  list_folders(): Promise<FolderDto[]>;
  list_conversations(folder_id: string): Promise<{ id: string; title: string; sort_order: number; updated?: number; ducky_style?: string; ducky_name?: string; ducky_personality?: string; tts_voice?: string; tts_speed?: number; file_path?: string; model?: string; provider?: string; coding_agent?: string; thinking_effort?: string; terminal_session_id?: string; folder_id?: string; parent_conv_id?: string; is_group?: boolean; is_subagent?: boolean; group_members?: GroupMemberDto[]; tool_call_count?: number; file_count?: number; context_tokens?: number }[]>;
  list_all_conversations(): Promise<{ id: string; title: string; sort_order: number; updated?: number; ducky_style?: string; ducky_name?: string; ducky_personality?: string; tts_voice?: string; tts_speed?: number; file_path?: string; model?: string; provider?: string; coding_agent?: string; thinking_effort?: string; terminal_session_id?: string; folder_id?: string; parent_conv_id?: string; is_group?: boolean; is_subagent?: boolean; group_members?: GroupMemberDto[]; tool_call_count?: number; file_count?: number; context_tokens?: number }[]>;
  list_conversations_for_file(file_path: string): Promise<{ id: string; title: string; sort_order: number; updated?: number; ducky_style?: string; ducky_name?: string; ducky_personality?: string; tts_voice?: string; tts_speed?: number; file_path?: string; model?: string; provider?: string; coding_agent?: string; thinking_effort?: string; terminal_session_id?: string; folder_id?: string; parent_conv_id?: string; is_group?: boolean; is_subagent?: boolean; group_members?: GroupMemberDto[]; tool_call_count?: number; file_count?: number; context_tokens?: number }[]>;
  apply_sidebar_layout(patch: SidebarLayoutPatch): Promise<void>;
  create_folder(name: string, parent_id?: string): Promise<{ id: string; name: string }>;
  rename_folder(folder_id: string, name: string): Promise<void>;
  /** Resolves to the group hub chat ids deleted along with the folder. */
  delete_folder(folder_id: string): Promise<string[]>;
  create_conversation(
    folder_id: string,
    ducky_style?: string,
    file_path?: string,
    config?: DuckyConfigDto,
  ): Promise<{ id: string; title: string; ducky_style?: string; ducky_name?: string; ducky_personality?: string; file_path?: string; model?: string; provider?: string; coding_agent?: string }>;
  apply_ducky_config(conv_id: string, config: DuckyConfigDto): Promise<{ ok: boolean; error?: string }>;
  group_create?(name?: string, folder_id?: string): Promise<{ ok?: boolean; id?: string; title?: string; is_group?: boolean; leader_conv_id?: string; group_members?: GroupMemberDto[]; folder_id?: string; error?: string }>;
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
  group_remove?(group_id: string, member_conv_id: string): Promise<{ ok?: boolean; group_members?: GroupMemberDto[]; leader_conv_id?: string; error?: string }>;
  group_members?(group_id: string): Promise<{ ok?: boolean; is_group?: boolean; leader_conv_id?: string; members?: GroupMemberDto[]; error?: string }>;
  group_add_member?(
    group_id: string,
    conv_id: string,
    as_leader?: boolean,
  ): Promise<{ ok?: boolean; leader_conv_id?: string; group_members?: GroupMemberDto[]; error?: string }>;
  list_agent_profiles(): Promise<AgentProfileListDto>;
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
  move_conversation(conv_id: string, folder_id: string): Promise<void>;
  delete_conversation(conv_id: string): Promise<void>;
  load_messages(conv_id: string): Promise<ChatMessage[]>;
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
  stop_verse_diagnostics_scan(project_root?: string): Promise<{ ok: boolean }>;
  report_open_tabs(window_id: string, tab_ids: string[]): Promise<void>;
  focus_tab(tab_id: string, requesting_window: string): Promise<{ ok: boolean; window_id: string }>;
  claim_tab(tab_id: string, window_id: string): Promise<void>;
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
  test_key(provider: string, key?: string): Promise<{ ok: boolean; detail: string }>;
  list_coding_agents(): Promise<{ agents: CodingAgentDto[] }>;
  set_conversation_coding_agent(
    conv_id: string,
    coding_agent: string,
    model?: string,
    provider?: string,
  ): Promise<{ ok: boolean; coding_agent?: string; model?: string; provider?: string; error?: string }>;
  set_conversation_thinking_effort(
    conv_id: string,
    effort: string,
  ): Promise<{ ok: boolean; thinking_effort?: string; error?: string }>;
  detect_coding_agent_cli(agent_id: string): Promise<Record<string, unknown>>;
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
      supports_vision?: boolean;
      supports_tools?: boolean;
      supports_web_search?: boolean;
      context_limit?: number;
      price_in?: number | null;
      price_out?: number | null;
      is_local?: boolean;
    }[]
  >;
  set_model(model_id: string, provider?: string): Promise<void>;
  send_message(
    conv_id: string,
    text: string,
    mode: AgentMode,
    model: string,
    file_path?: string,
    attachments?: MessageAttachmentDto[],
  ): Promise<{ run_id: string }>;
  resend_last_user_message(
    conv_id: string,
    text: string,
    mode: AgentMode,
    model: string,
    file_path?: string,
    attachments?: MessageAttachmentDto[],
  ): Promise<{ run_id: string }>;
  get_context_usage(
    conv_id: string,
    model: string,
    mode?: AgentMode,
    draft?: string,
    include_content?: boolean,
  ): Promise<ContextUsage>;
  get_provider_usage(provider_id?: string, days?: number): Promise<ProviderUsageReport>;
  get_ducky_usage(ducky_name?: string, profile_id?: string, days?: number): Promise<DuckyUsageReport>;
  reset_context(conv_id: string, segments: string[], mode?: AgentMode, model?: string): Promise<ContextControlResult>;
  restore_context(conv_id: string, segments: string[], mode?: AgentMode, model?: string): Promise<ContextControlResult>;
  get_session_files(conv_id: string): Promise<SessionFile[]>;
  cancel_agent(conv_id?: string): Promise<boolean>;
  /** Wait until the agent thread for this chat is gone (e.g. after Stop). */
  wait_for_agent_idle?(conv_id: string, timeout?: number): Promise<boolean>;
  report_ui_perf(entries: Array<Record<string, unknown>>): Promise<boolean>;
  ui_rpc_respond(request_id: string, payload: Record<string, unknown>): Promise<boolean>;
  list_running_agents(): Promise<string[]>;
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
  clear_errors(): Promise<string[]>;
  pull_editor_log(): Promise<void>;
  open_appdata(): Promise<void>;
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
  terminal_list(): Promise<{ sessions: Record<string, unknown>[] }>;
  terminal_write(session_id: string, data: string): Promise<Record<string, unknown>>;
  terminal_resize(session_id: string, cols: number, rows: number): Promise<Record<string, unknown>>;
  terminal_request_command(
    session_id: string,
    command: string,
    source?: string,
    conv_id?: string,
  ): Promise<Record<string, unknown>>;
  terminal_approve_command(request_id: string): Promise<Record<string, unknown>>;
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
  version: number;
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
  version: number;
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
  version: number;
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

export interface McpCategoryDto {
  id: string;
  label: string;
  tools: McpToolDto[];
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
  listener_online?: boolean;
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
