/**
 * Spoken purpose + result lines for live-voice tool chatter.
 * Rule-based first; opaque blobs opt into an LLM summary.
 */

import type { ToolCallData } from "../types/panel";
import { humanToolLabel } from "../utils/agentActivity";

const TOOL_PURPOSE: Record<string, string> = {
  workspace_read_file: "reading the file",
  workspace_write_file: "writing the file",
  workspace_list_verse_errors: "checking Verse errors",
  workspace_list_dir: "listing directories",
  workspace_compile_verse: "compiling Verse",
  create_verse_device: "creating a Verse device",
  list_verse_devices: "listing Verse devices",
  ducky_get_local_project: "reading the local project",
  project_memory_list: "listing project memory",
  project_memory_get: "reading project memory",
  project_memory_save: "saving project memory",
  project_memory_append: "updating project memory",
  project_memory_delete: "deleting a memory entry",
  ducky_memory_overview: "surveying ducky memories",
  ping: "checking the UEFN listener",
  inspect_verse_device: "inspecting a Verse device",
  get_all_actors: "querying actors",
  find_devices: "finding devices",
  search_assets: "searching the asset registry",
  execute_python: "running editor Python",
  save_current_level: "saving the level",
  take_high_res_screenshot: "capturing a screenshot",
  create_landscape: "generating landscape",
  sculpt_landscape: "sculpting terrain",
  get_ground_z: "raycasting ground height",
  set_viewport_camera: "moving the editor camera",
  spawn_actor: "spawning an actor",
  get_level_bounds: "reading level bounds",
  Skill: "retrieving agent knowledge",
  read: "reading the file",
  edit: "editing the file",
  write: "writing the file",
  Glob: "scanning files",
  Grep: "searching file contents",
  ls: "listing directories",
  grep: "searching file contents",
  glob: "scanning files",
  semSearch: "searching the codebase",
  shell: "running a shell command",
  ToolSearch: "searching the tool registry",
  Bash: "running a shell command",
  PowerShell: "running PowerShell",
  file_change: "editing files",
  web_search: "searching the web",
  duplicate_asset: "duplicating an asset",
  create_anim_preset: "creating an animation preset",
  create_character_blueprint: "creating a character blueprint",
  create_npc_character_definition: "creating an NPC definition",
  create_physics_asset_for_mesh: "creating a physics asset",
  set_npc_definition_behavior: "setting NPC behavior",
  set_npc_spawner_definition: "assigning the NPC spawner",
  verse_template_apply: "applying a Verse template",
  ducky_create_plan: "creating a plan",
  ducky_plan_update_node: "updating a plan step",
  ducky_get_tools: "listing tools",
  ducky_find_tools: "finding tools",
  skill_read_subskill: "reading a skill",
  get_verse_api: "looking up the Verse API",
  search_verse_digest: "searching Verse digests",
  npc_author_capabilities: "checking NPC authoring tools",
};

function bareName(toolName: string): string {
  return (toolName || "").replace(/^mcp__uefn__/i, "").replace(/^mcp__[^_]+__/i, "").trim();
}

function purposeOf(toolName: string): string {
  const bare = bareName(toolName);
  return TOOL_PURPOSE[toolName] ?? TOOL_PURPOSE[bare] ?? humanToolLabel(bare || "something").toLowerCase();
}

function spokenBasename(path: string): string {
  const base = path.replace(/\\/g, "/").split("/").filter(Boolean).pop() || path;
  return base.replace(/\./g, " dot ");
}

function salientArg(args: Record<string, unknown> | undefined): string {
  if (!args || typeof args !== "object") return "";
  const path = args.relative_path ?? args.path ?? args.destination_path ?? args.asset_path;
  if (typeof path === "string" && path.trim()) return spokenBasename(path.trim());
  const command = args.command;
  if (typeof command === "string" && command.trim()) {
    const short = command.trim().length > 48 ? `${command.trim().slice(0, 47).trim()}…` : command.trim();
    return short;
  }
  const query = args.query ?? args.q ?? args.search ?? args.name_filter;
  if (typeof query === "string" && query.trim()) return query.trim();
  return "";
}

/** "Writing the file prey dot verse." */
export function spokenToolStart(tool: Pick<ToolCallData, "name" | "arguments"> | undefined | null): string {
  const name = tool?.name || "something";
  const purpose = purposeOf(name);
  const arg = salientArg(tool?.arguments);
  return arg ? `${purpose.charAt(0).toUpperCase()}${purpose.slice(1)} ${arg}.` : `${purpose.charAt(0).toUpperCase()}${purpose.slice(1)}.`;
}

function tryParseJson(raw: string): unknown {
  const trimmed = raw.trim();
  if (!trimmed || (trimmed[0] !== "{" && trimmed[0] !== "[")) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    return null;
  }
}

function shortError(text: string): string {
  const one = text.replace(/\s+/g, " ").trim();
  return one.length > 120 ? `${one.slice(0, 119).trim()}…` : one;
}

function countFrom(data: unknown): number | null {
  if (Array.isArray(data)) return data.length;
  if (!data || typeof data !== "object") return null;
  const rec = data as Record<string, unknown>;
  for (const key of ["count", "total", "length", "n"]) {
    const v = rec[key];
    if (typeof v === "number" && Number.isFinite(v)) return v;
  }
  if (Array.isArray(rec.assets)) return rec.assets.length;
  if (Array.isArray(rec.items)) return rec.items.length;
  if (Array.isArray(rec.results)) return rec.results.length;
  if (Array.isArray(rec.rows)) return rec.rows.length;
  return null;
}

function writtenPathFrom(data: unknown, args?: Record<string, unknown>): string {
  if (data && typeof data === "object") {
    const rec = data as Record<string, unknown>;
    for (const key of ["path", "relative_path", "asset_path", "written"]) {
      if (typeof rec[key] === "string" && rec[key].trim()) return spokenBasename(rec[key].trim());
    }
  }
  const fallback = args?.relative_path ?? args?.path ?? args?.destination_path;
  return typeof fallback === "string" && fallback.trim() ? spokenBasename(fallback.trim()) : "";
}

function lineCountFrom(data: unknown, raw: string): number | null {
  if (data && typeof data === "object") {
    const rec = data as Record<string, unknown>;
    for (const key of ["lines", "line_count", "lineCount"]) {
      const v = rec[key];
      if (typeof v === "number" && Number.isFinite(v)) return v;
    }
  }
  const n = raw.split(/\r?\n/).filter((l) => l.length > 0).length;
  return n > 1 ? n : null;
}

/** Rule-based digest of a tool result. */
export function spokenToolResult(tool: ToolCallData | undefined | null): string {
  if (!tool) return "Done.";
  const status = (tool.status || "").toLowerCase();
  const raw = (tool.result || "").trim();
  if (status === "error" || status === "cancelled") {
    const err = shortError(raw || tool.hint || status);
    return err ? `Failed: ${err}` : "Failed.";
  }
  const data = raw ? tryParseJson(raw) : null;
  const path = writtenPathFrom(data, tool.arguments);
  const lines = lineCountFrom(data, typeof data === "string" ? data : raw);
  if (path && /write|edit|duplicate|create|save/i.test(tool.name || "")) {
    return lines && lines > 1 ? `Wrote ${path}, ${lines} lines.` : `Wrote ${path}.`;
  }
  const count = countFrom(data);
  if (count != null) {
    const noun = count === 1 ? "result" : "results";
    return `Found ${count} ${noun}.`;
  }
  if (data && typeof data === "object" && !Array.isArray(data)) {
    const rec = data as Record<string, unknown>;
    if (rec.ok === true || rec.success === true) {
      return path ? `Done, ${path}.` : "Done.";
    }
    if (rec.ok === false || rec.success === false) {
      const err = typeof rec.error === "string" ? shortError(rec.error) : "";
      return err ? `Failed: ${err}` : "Failed.";
    }
  }
  if (raw && raw.length <= 160 && !data) {
    return raw.endsWith(".") ? raw : `${raw}.`;
  }
  if (path) return `Done, ${path}.`;
  return "Done.";
}

/** True when the result is a JSON blob or long prose with no known shape. */
export function needsLlmSummary(tool: ToolCallData | undefined | null): boolean {
  if (!tool) return false;
  const status = (tool.status || "").toLowerCase();
  if (status === "error" || status === "cancelled") return false;
  const raw = (tool.result || "").trim();
  if (!raw) return false;
  const data = tryParseJson(raw);
  if (data && typeof data === "object") {
    const count = countFrom(data);
    const rec = data as Record<string, unknown>;
    const shaped =
      count != null ||
      rec.ok === true ||
      rec.ok === false ||
      rec.success === true ||
      rec.success === false ||
      typeof rec.path === "string" ||
      typeof rec.relative_path === "string";
    if (!shaped) return true;
    return false;
  }
  return raw.length > 280;
}
