/**
 * Which plugin a tool belongs to, and loopback links for the media files its result names,
 * for the tool cards in chat. One panel call per burst of cards (every card that mounts in
 * the same 40 ms shares it), answers kept for the session.
 */
import { useEffect, useMemo, useState } from "react";
import { getApi } from "./usePanelApi";

export type ToolOwner = { plugin_id: string; label: string; panel_id: string; panel_title: string };
export type MediaLink = { path: string; url: string; kind: string; name: string };

type ChatToolViewApi = {
  chat_tool_view?(tool_names: string[], paths: string[]): Promise<{
    tools?: Record<string, Partial<ToolOwner>>;
    media?: Array<MediaLink & { asked?: string }>;
  }>;
};

const owners = new Map<string, ToolOwner | null>();
const links = new Map<string, MediaLink | null>();
const wantTools = new Set<string>();
const wantPaths = new Set<string>();
// Asked and not answered yet: a card mounting meanwhile waits for that answer.
const askedTools = new Set<string>();
const askedPaths = new Set<string>();
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setTimeout> | null = null;

function notify(): void {
  for (const fn of listeners) fn();
}

async function flush(): Promise<void> {
  timer = null;
  const tools = [...wantTools];
  const paths = [...wantPaths];
  wantTools.clear();
  wantPaths.clear();
  if (!tools.length && !paths.length) return;
  for (const name of tools) askedTools.add(name);
  for (const path of paths) askedPaths.add(path);
  const api = getApi() as unknown as ChatToolViewApi | null;
  let out: Awaited<ReturnType<NonNullable<ChatToolViewApi["chat_tool_view"]>>> = {};
  try {
    out = (await api?.chat_tool_view?.(tools, paths)) ?? {};
  } catch {
    out = {};  // an older app or a phone: no owners, no links (asked once)
  }
  for (const name of tools) {
    const row = out.tools?.[name];
    owners.set(name, row?.plugin_id ? {
      plugin_id: String(row.plugin_id),
      label: String(row.label || row.plugin_id),
      panel_id: String(row.panel_id || ""),
      panel_title: String(row.panel_title || row.label || row.plugin_id),
    } : null);
  }
  for (const path of paths) links.set(path, null);
  for (const row of out.media ?? []) {
    if (row?.url && row.asked) links.set(row.asked, { path: row.path, url: row.url, kind: row.kind, name: row.name });
  }
  for (const name of tools) askedTools.delete(name);
  for (const path of paths) askedPaths.delete(path);
  notify();
}

function ask(tools: string[], paths: string[]): void {
  let added = false;
  for (const name of tools) {
    if (name && !owners.has(name) && !wantTools.has(name) && !askedTools.has(name)) {
      wantTools.add(name);
      added = true;
    }
  }
  for (const path of paths) {
    if (path && !links.has(path) && !wantPaths.has(path) && !askedPaths.has(path)) {
      wantPaths.add(path);
      added = true;
    }
  }
  if (added && !timer) timer = setTimeout(() => void flush(), 40);
}

function useAnswers(): number {
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const fn = () => setVersion((v) => v + 1);
    listeners.add(fn);
    return () => {
      listeners.delete(fn);
    };
  }, []);
  return version;
}

/** Owners by bare tool name (null: one of Ducky's own tools, or not answered yet). */
export function useToolOwners(names: string[]): Record<string, ToolOwner | null> {
  const key = names.join("\n");
  const version = useAnswers();
  useEffect(() => {
    if (key) ask(key.split("\n"), []);
  }, [key]);
  return useMemo(() => {
    const out: Record<string, ToolOwner | null> = {};
    for (const name of key ? key.split("\n") : []) out[name] = owners.get(name) ?? null;
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, version]);
}

/** Links for the paths that are media files on this PC, in the order asked. */
export function useMediaLinks(paths: string[]): MediaLink[] {
  const key = paths.join("\n");
  const version = useAnswers();
  useEffect(() => {
    if (key) ask([], key.split("\n"));
  }, [key]);
  return useMemo(() => {
    const seen = new Set<string>();
    const out: MediaLink[] = [];
    for (const path of key ? key.split("\n") : []) {
      const link = links.get(path);
      if (link && !seen.has(link.url)) {
        seen.add(link.url);
        out.push(link);
      }
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, version]);
}

/** Tests: forget every answer. */
export function resetToolViewCache(): void {
  owners.clear();
  links.clear();
  wantTools.clear();
  wantPaths.clear();
  askedTools.clear();
  askedPaths.clear();
  if (timer) clearTimeout(timer);
  timer = null;
}
