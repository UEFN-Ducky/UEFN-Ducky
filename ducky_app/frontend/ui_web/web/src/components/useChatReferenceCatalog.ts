import { useEffect, useMemo, useState } from "react";
import { kindTitle } from "../plugin-ui/scopeBarText";
import { getApi } from "../hooks/usePanelApi";
import { usePluginContributions } from "../hooks/usePluginContributions";
import { chatMentionGroup, dedupeChatRefs, filterChatRefs, orderChatMentions, type ChatRef } from "./chatReferences";
import { noteChatRefFaces } from "./chatRefFaces";
import { resolvePluginIconContent } from "../hooks/pluginHeaderActions";
import { ensureLoaded, useChatReferenceStore } from "./chatReferenceStore";
import { isAbsEncodedPath, isFolderProject } from "../verse-editor/utils/isVerseFile";

interface ConvRow {
  id: string;
  title?: string;
  folder_id?: string;
  is_group?: boolean;
  project_slug?: string;
  project_name?: string;
  ducky_style?: string;
}

function isArchived(row: ConvRow): boolean {
  return (row.folder_id || "").trim() === "archive";
}

function chatRef(
  trigger: ChatRef["trigger"],
  id: string,
  label: string,
  group: string,
  href: string,
  description?: string,
  face?: { duckyStyle?: string; iconUrl?: string },
): ChatRef {
  return { trigger, id, label, group, href, description, ...face };
}

const CONTENT_TEXT = /\.(verse|versetest|vson|json|md|txt|toml|cfg)$/i;

/** `list_project_file_paths` replies `{paths, total, truncated}` (first 2,000 paths only). */
export function filePathsOf(reply: unknown): string[] {
  const paths = (reply as { paths?: unknown } | null)?.paths;
  return Array.isArray(paths) ? paths.map((path) => String(path || "")) : [];
}

/** Text files a chat can reference; the name is the last path segment. */
export function fileRefsOf(paths: string[]): ChatRef[] {
  const files: ChatRef[] = [];
  for (const raw of paths) {
    const path = raw.replace(/\\/g, "/");
    const name = path.split("/").pop() || "";
    const inContent = isFolderProject() ? !isAbsEncodedPath(path) : path.startsWith("Content/");
    if (!name || !inContent || !CONTENT_TEXT.test(name)) continue;
    if (path.includes(".digest.verse")) continue;
    files.push(chatRef("/", `file:${path}`, name, "Content", `file:${path}`, path));
  }
  return files;
}

const DEEP_FILE_MIN_HITS = 10;

// ponytail: the reply type in types/panel.ts still says the old row list; read it as unknown here.
type FilePathsApi = { list_project_file_paths(query?: string, limit?: number): Promise<unknown> };

/**
 * Files matching *query*: the local list first; under 10 hits, the server search
 * (`list_project_file_paths(query, 50)`, debounced 200 ms) finds files past the first 2,000.
 */
export function useFileRefMatches(query: string, enabled: boolean, files: ChatRef[]): ChatRef[] {
  const q = enabled ? query.trim() : "";
  const local = useMemo(() => (q ? filterChatRefs(files, q) : []), [q, files]);
  const [deep, setDeep] = useState<{ q: string; rows: ChatRef[] }>({ q: "", rows: [] });
  const needDeep = !!q && local.length < DEEP_FILE_MIN_HITS;
  useEffect(() => {
    if (!needDeep) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void (getApi() as unknown as FilePathsApi | null)
        ?.list_project_file_paths(q, 50)
        .then((reply) => {
          if (!cancelled) setDeep({ q, rows: fileRefsOf(filePathsOf(reply)) });
        })
        .catch(() => {});
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [q, needDeep]);
  return useMemo(
    () => (needDeep && deep.q === q ? dedupeChatRefs([...local, ...deep.rows]) : local),
    [needDeep, deep, q, local],
  );
}

/**
 * Ranks the shared catalog (chatReferenceStore) for *chatId*. It never fetches by itself
 * unless *active*: the composer passes `active` once its text has an '@' or '/', or calls
 * `ensureLoaded()` itself, so mounting or switching chats costs no calls. `loading` is true
 * while the first load runs (show "Loading…" rows).
 */
export function useChatReferenceCatalog(
  active: boolean,
  chatId: string,
): { mentions: ChatRef[]; slashRefs: ChatRef[]; files: ChatRef[]; loading: boolean } {
  const contrib = usePluginContributions();
  const { status, data } = useChatReferenceStore();

  useEffect(() => {
    if (active) ensureLoaded();
  }, [active]);

  const builtinMentions = useMemo(() => {
    const convs = (data.convs ?? []) as ConvRow[];
    const openSlug = String(
      convs.find((row) => row.id === chatId)?.project_slug
        || (data.recents ?? []).find((row) => row.active)?.slug
        || "",
    ).trim();
    const thisProject: ChatRef[] = [];
    const otherProjects = new Map<string, ChatRef[]>();
    const globalMentions: ChatRef[] = [];
    for (const conv of convs) {
      if (!conv.id || isArchived(conv)) continue;
      const slug = String(conv.project_slug || "").trim();
      const title = String(conv.title || "Ducky").trim() || "Ducky";
      const face = { duckyStyle: String(conv.ducky_style || "").trim() || undefined };
      const group = chatMentionGroup(slug, openSlug, String(conv.project_name || ""));
      const row = chatRef("@", `chat:${conv.id}`, title, group, `ducky:${conv.id}`, undefined, face);
      if (group === "This project") thisProject.push(row);
      else if (group === "Global") globalMentions.push(row);
      else {
        const list = otherProjects.get(group) ?? [];
        list.push(row);
        otherProjects.set(group, list);
      }
    }
    for (const profile of data.profiles?.profiles ?? []) {
      const id = String(profile.id || "").trim();
      if (!id || id === "__blank__") continue;
      globalMentions.push(chatRef("@", `profile:${id}`, profile.name || id, "Global", `profile:${id}`, profile.when_to_use, {
        duckyStyle: profile.ducky_style,
      }));
    }
    return orderChatMentions([
      ...thisProject,
      ...[...otherProjects.values()].flat(),
      ...globalMentions,
    ]);
  }, [data.convs, data.recents, data.profiles, chatId]);

  const builtinSlash = useMemo(() => {
    const slash: ChatRef[] = [];
    for (const workflow of data.workflows?.workflows || []) {
      const owner = workflow.owner?.kind === "shared" ? `${kindTitle(workflow.owner.kindLabel)} ${workflow.owner.label}` : "Local";
      const about = workflow.description || "Run this workflow with the request you type after it";
      slash.push(chatRef("/", `workflow:${workflow.id}`, workflow.name || "Untitled workflow", "Workflows", `workflow:${workflow.id}`, `${owner} · ${about}`));
    }
    for (const pack of data.skills?.packs ?? []) {
      const id = String(pack.id || "").trim();
      if (!id) continue;
      slash.push(chatRef("/", `skill:${id}`, pack.label || id, "Skills", `skill:${id}`, pack.description));
      for (const sub of pack.subskills ?? []) {
        const subId = String(sub.id || "").trim();
        if (!subId || subId === "core") continue;
        slash.push(
          chatRef(
            "/",
            `subskill:${id}/${subId}`,
            sub.label || subId,
            "Subskills",
            `subskill:${id}/${subId}`,
            pack.label || id,
          ),
        );
      }
    }
    for (const server of data.mcp?.plugins ?? []) {
      const id = String(server.id || "").trim();
      if (!id) continue;
      slash.push(chatRef("/", `mcp:${id}`, server.label || id, "MCP", `mcp:${id}`, server.description));
    }
    // Plugin panels come from contributions: they show even before the catalog loads.
    for (const panel of contrib.ui_panels ?? []) {
      const pluginId = String(panel.plugin_id || "").trim();
      const panelId = String(panel.id || "").trim();
      if (!pluginId || !panelId) continue;
      slash.push(
        chatRef(
          "/",
          `plugin:${pluginId}/${panelId}`,
          panel.title || panelId,
          "Plugins",
          `plugin:${pluginId}/${panelId}`,
          undefined,
          {
            iconUrl: (() => {
              const icon = resolvePluginIconContent(panel.icon);
              return icon.kind === "image" ? icon.src : icon.emoji;
            })(),
          },
        ),
      );
    }
    return slash;
  }, [data.workflows, data.skills, data.mcp, contrib.ui_panels]);

  const files = useMemo(() => fileRefsOf(filePathsOf(data.paths)), [data.paths]);

  const { mentions, slashRefs } = useMemo(() => {
    const extra: ChatRef[] = [];
    for (const row of contrib.chat_references ?? []) {
      const trigger = row.trigger === "@" ? "@" : row.trigger === "/" ? "/" : null;
      const id = String(row.id || "").trim();
      const label = String(row.label || "").trim();
      const href = String(row.href || "").trim();
      if (!trigger || !id || !label || !href) continue;
      extra.push({
        trigger,
        id,
        label,
        href,
        group: String(row.group || "Plugins").trim() || "Plugins",
        description: row.description,
      });
    }
    for (const row of data.pluginRefs?.refs ?? []) {
      extra.push(chatRef(row.trigger, row.id, row.label, row.group || "Plugins", row.href, row.description, { iconUrl: row.icon_url }));
    }
    return {
      mentions: dedupeChatRefs([...builtinMentions, ...extra.filter((row) => row.trigger === "@")]),
      slashRefs: dedupeChatRefs([...builtinSlash, ...extra.filter((row) => row.trigger === "/")]),
    };
  }, [builtinMentions, builtinSlash, contrib.chat_references, data.pluginRefs]);

  useEffect(() => {
    noteChatRefFaces([...mentions, ...slashRefs]);
  }, [mentions, slashRefs]);
  return { mentions, slashRefs, files, loading: status === "loading" };
}
