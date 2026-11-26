import type { OpenFileHandler } from "../../types/richContent";
import type { RichBlock } from "../../types/richContent";
import type { ComponentType } from "react";
import { SwarmCard, countSeats, parseSwarmResult } from "./SwarmCard";
import { SwarmBuilderToolView, swarmBuilderPayload } from "../../swarm/SwarmBuilderCard";
import { FinishTeamToolView, parseFinishTeam } from "./FinishTeamCard";

export interface ToolPresenterContext {
  toolName: string;
  arguments: Record<string, unknown>;
  resultText: string;
  isSuccess: boolean;
  onOpenFile?: OpenFileHandler;
}

export type ToolPresenter = (ctx: ToolPresenterContext) => RichBlock[] | null;

function parseResultJson(raw: string): unknown {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    return null;
  }
}

function unwrapData(parsed: unknown): unknown {
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return parsed;
  const obj = parsed as Record<string, unknown>;
  if (obj.ok === true && "data" in obj) return obj.data;
  if ("data" in obj) return obj.data;
  return parsed;
}

function pickColumns(rows: Record<string, unknown>[], preferred: string[]): string[] {
  if (rows.length === 0) return preferred;
  const keys = new Set<string>();
  for (const row of rows) Object.keys(row).forEach((k) => keys.add(k));
  const cols = preferred.filter((k) => keys.has(k));
  if (cols.length > 0) return cols;
  return Array.from(keys).slice(0, 6);
}

function tableFromObjects(
  rows: Record<string, unknown>[],
  columns: string[],
): RichBlock | null {
  if (rows.length === 0) return { type: "paragraph", text: "(no results)" };
  const headers = pickColumns(rows, columns);
  const tableRows = rows.slice(0, 50).map((row) =>
    headers.map((h) => {
      const v = row[h];
      if (v === null || v === undefined) return "";
      return typeof v === "object" ? JSON.stringify(v) : String(v);
    }),
  );
  return { type: "table", headers, rows: tableRows };
}

const deviceListPresenter: ToolPresenter = ({ resultText }) => {
  const data = unwrapData(parseResultJson(resultText));
  let rows: Record<string, unknown>[] = [];
  if (Array.isArray(data)) {
    rows = data as Record<string, unknown>[];
  } else if (data && typeof data === "object") {
    const obj = data as Record<string, unknown>;
    const list = obj.actors ?? obj.devices ?? obj.items ?? obj.results;
    if (Array.isArray(list)) rows = list as Record<string, unknown>[];
  }
  const table = tableFromObjects(rows, ["label", "name", "kind", "class", "class_name", "path"]);
  return table ? [table] : null;
};

const inspectPresenter: ToolPresenter = ({ resultText }) => {
  const data = unwrapData(parseResultJson(resultText));
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  const obj = data as Record<string, unknown>;
  const blocks: RichBlock[] = [];

  const topPairs = Object.entries(obj)
    .filter(([k]) => !["settings", "fields", "editables", "wiring"].includes(k))
    .filter(([, v]) => v === null || ["string", "number", "boolean"].includes(typeof v))
    .map(([key, value]) => ({ key, value: String(value ?? "") }));
  if (topPairs.length > 0) blocks.push({ type: "key_value", pairs: topPairs });

  for (const section of ["settings", "fields", "editables", "wiring"] as const) {
    const val = obj[section];
    if (val === undefined) continue;
    if (val && typeof val === "object" && !Array.isArray(val)) {
      const pairs = Object.entries(val as Record<string, unknown>)
        .slice(0, 40)
        .map(([key, value]) => ({
          key,
          value: typeof value === "object" ? JSON.stringify(value) : String(value ?? ""),
        }));
      blocks.push({ type: "accordion", title: section, blocks: [{ type: "key_value", pairs }] });
    } else {
      blocks.push({
        type: "accordion",
        title: section,
        blocks: [{ type: "code", language: "json", text: JSON.stringify(val, null, 2) }],
      });
    }
  }
  return blocks.length > 0 ? blocks : null;
};

const fileContentPresenter: ToolPresenter = ({ resultText, arguments: args }) => {
  const data = unwrapData(parseResultJson(resultText));
  const rel =
    (typeof args.relative_path === "string" && args.relative_path) ||
    (typeof args.path === "string" && args.path) ||
    "";
  const blocks: RichBlock[] = [];
  if (rel) blocks.push({ type: "file_link", path: rel });

  let content = "";
  if (typeof data === "string") content = data;
  else if (data && typeof data === "object" && !Array.isArray(data)) {
    const obj = data as Record<string, unknown>;
    content = String(obj.content ?? obj.text ?? obj.data ?? "");
  } else if (data === null && resultText.trim()) {
    // Plain-text reads: "path  lines a-b of N" header, then the file text.
    const [first, ...rest] = resultText.split("\n");
    content = /\slines \d+-\d+ of \d+/.test(first) ? rest.join("\n") : resultText;
  }
  if (content) {
    const lang = rel.toLowerCase().endsWith(".verse") ? "verse" : undefined;
    blocks.push({ type: "code", language: lang, text: content });
  }
  if (blocks.length === 0) return null;
  return blocks;
};

const assetListPresenter: ToolPresenter = ({ resultText }) => {
  const data = unwrapData(parseResultJson(resultText));
  let rows: Record<string, unknown>[] = [];
  if (Array.isArray(data)) {
    rows = data as Record<string, unknown>[];
  } else if (data && typeof data === "object") {
    const obj = data as Record<string, unknown>;
    const list = obj.assets ?? obj.items ?? obj.results;
    if (Array.isArray(list)) rows = list as Record<string, unknown>[];
  }
  const table = tableFromObjects(rows, ["name", "asset_name", "class", "class_name", "path", "package_path"]);
  return table ? [table] : null;
};

const verseErrorsPresenter: ToolPresenter = ({ resultText, onOpenFile }) => {
  const data = unwrapData(parseResultJson(resultText));
  const blocks: RichBlock[] = [];
  let errors: Record<string, unknown>[] = [];

  if (Array.isArray(data)) errors = data as Record<string, unknown>[];
  else if (data && typeof data === "object") {
    const obj = data as Record<string, unknown>;
    if (obj.lsp === "remote") {
      // macOS: no verse-lsp here, so Verse is checked on the account's PC. An empty list is
      // "not checked", never "no errors".
      blocks.push({ type: "callout", tone: "info", text: String(obj.note || "Verse diagnostics run on your PC.") });
      for (const pc of Array.isArray(obj.open_remote) ? (obj.open_remote as Record<string, unknown>[]) : []) {
        if (pc && typeof pc.key_id === "string" && pc.key_id) {
          blocks.push({ type: "ducky_link", key_id: pc.key_id, name: String(pc.name || "your PC") });
        }
      }
      return blocks;
    }
    if (Array.isArray(obj.files)) {
      // Raw tool shape: { files: [{ path, errors, warnings, items: [{line, column, message, severity}] }] }.
      // Flatten per-file items into rows; the old errors/diagnostics lookup missed
      // this shape entirely and rendered "No Verse errors found." over real errors.
      for (const f of obj.files as Record<string, unknown>[]) {
        if (!f || typeof f !== "object") continue;
        const path = typeof f.path === "string" ? f.path : "";
        const items = Array.isArray(f.items) ? (f.items as Record<string, unknown>[]) : [];
        for (const it of items) {
          if (!it || typeof it !== "object") continue;
          errors.push({ file: path, ...it });
        }
      }
    } else {
      const list = obj.problems ?? obj.errors ?? obj.diagnostics ?? obj.items;
      if (Array.isArray(list)) errors = list as Record<string, unknown>[];
    }
  }

  if (errors.length === 0) {
    blocks.push({ type: "callout", tone: "success", text: "No Verse errors found." });
    return blocks;
  }

  blocks.push({ type: "callout", tone: "error", text: `${errors.length} Verse error(s)` });
  const table = tableFromObjects(errors, ["file", "path", "line", "column", "message", "severity"]);
  if (table) blocks.push(table);

  const firstFile = errors.find((e) => typeof e.file === "string" || typeof e.path === "string");
  const filePath = (firstFile?.file ?? firstFile?.path) as string | undefined;
  if (filePath && onOpenFile) {
    const line = typeof firstFile?.line === "number" ? firstFile.line : undefined;
    blocks.push({
      type: "file_link",
      path: filePath,
      label: line ? `${filePath}:${line}` : filePath,
    });
  }
  return blocks;
};

/** Plain-block fallback (search cards, copies); the chat draws SwarmCard via TOOL_VIEWS. */
const swarmPresenter: ToolPresenter = ({ resultText }) => {
  const data = parseSwarmResult(resultText);
  if (!data) return null;
  const spec = { type: "tree", root: { label: data.goal ? `Lead · ${data.goal}` : "Lead", children: [
    ...data.groups.map((g) => ({ label: `${g.name} · ${countSeats(g)} agents${g.muted ? " · MUTED" : ""}`, color: g.muted ? "amber" : "purple" })),
    ...data.seats.map((s) => ({ label: [s.name, s.profile, s.model].filter(Boolean).join(" · "), color: "green" })),
  ] } };
  return [{ type: "code", language: "chart", text: JSON.stringify(spec) }];
};

type ToolView = { view: ComponentType<ToolPresenterContext>; accepts: (resultText: string) => boolean };

const TOOL_VIEWS: Record<string, ToolView[]> = {
  ducky_swarm_create: [
    { view: SwarmCard, accepts: (text) => parseSwarmResult(text) !== null },
    // Still asking (needs_answers): the sliders card, so the person shapes the team in place.
    { view: SwarmBuilderToolView, accepts: (text) => swarmBuilderPayload(text)?.status === "needs_answers" },
  ],
  ducky_finish_team: [{ view: FinishTeamToolView, accepts: (text) => parseFinishTeam(text) !== null }],
};

function bareToolName(name: string): string {
  const cut = name.lastIndexOf("__");
  return cut >= 0 ? name.slice(cut + 2) : name;
}

/** A tool result drawn as its own React card (it can animate and send replies); null → blocks. */
export function resolveToolPresenterView(toolName: string, resultText: string): ComponentType<ToolPresenterContext> | null {
  return (TOOL_VIEWS[bareToolName(toolName)] ?? []).find((entry) => entry.accepts(resultText))?.view ?? null;
}

const PRESENTERS: Record<string, ToolPresenter> = {
  ducky_swarm_create: swarmPresenter,
  find_devices: deviceListPresenter,
  get_all_actors: deviceListPresenter,
  inspect_verse_device: inspectPresenter,
  inspect_creative_device: inspectPresenter,
  workspace_read_file: fileContentPresenter,
  list_assets: assetListPresenter,
  search_assets: assetListPresenter,
  workspace_list_verse_errors: verseErrorsPresenter,
};

export function resolveToolPresenterBlocks(ctx: ToolPresenterContext): RichBlock[] | null {
  const presenter = PRESENTERS[ctx.toolName] ?? PRESENTERS[bareToolName(ctx.toolName)];
  if (!presenter) return null;
  return presenter(ctx);
}
