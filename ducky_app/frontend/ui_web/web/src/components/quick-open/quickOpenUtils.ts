import type { ChatTab, EditorTab, FolderItem } from "../../types/panel";

export type QuickOpenMode = "file" | "text" | "ducky" | "command" | "symbol";

export function resolveQuickOpenQuery(
  query: string,
  fallbackMode: QuickOpenMode,
): { mode: QuickOpenMode; stripped: string } {
  if (query.startsWith("%")) return { mode: "text", stripped: query.slice(1).trim() };
  if (query.startsWith(">")) return { mode: "command", stripped: query.slice(1).trim() };
  if (query.startsWith("@")) return { mode: "symbol", stripped: query.slice(1).trim() };
  return { mode: fallbackMode, stripped: query.trim() };
}

export interface QuickOpenFileItem {
  kind: "file";
  path: string;
  name: string;
  score: number;
}

export interface QuickOpenChatItem {
  kind: "chat";
  id: string;
  name: string;
  duckyStyle?: string;
  score: number;
}

export interface QuickOpenRecentItem {
  kind: "recent";
  tab: EditorTab;
}

export type QuickOpenListItem = QuickOpenFileItem | QuickOpenChatItem | QuickOpenRecentItem;

export function fuzzyScore(query: string, text: string): number {
  const q = query.trim().toLowerCase();
  if (!q) return 0;
  const t = text.toLowerCase();

  // Multi-word queries ("player manager") match when every whitespace-separated
  // token matches. Without this, the literal space can never line up against a
  // name like "PlayerManager", so the whole query scores 0 and finds nothing.
  const tokens = q.split(/\s+/).filter(Boolean);
  if (tokens.length > 1) {
    let total = 0;
    for (const token of tokens) {
      const s = scoreToken(token, t);
      if (s <= 0) return 0;
      total += s;
    }
    return total;
  }

  return scoreToken(q, t);
}

/** Score a single whitespace-free token (already lowercased) against text (already lowercased). */
function scoreToken(q: string, t: string): number {
  if (t === q) return 1000;
  if (t.startsWith(q)) return 800 + (q.length / t.length) * 100;
  if (t.includes(q)) return 500 + (q.length / t.length) * 50;

  let qi = 0;
  let score = 0;
  let lastMatch = -1;
  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] === q[qi]) {
      score += lastMatch === ti - 1 ? 12 : 4;
      lastMatch = ti;
      qi++;
    }
  }
  return qi === q.length ? score : 0;
}

export function flattenChats(folders: FolderItem[], rootChats: FolderItem["chats"]): ChatTab[] {
  const out: ChatTab[] = [...rootChats];
  const walk = (items: FolderItem[]) => {
    for (const folder of items) {
      out.push(...folder.chats);
      walk(folder.children);
    }
  };
  walk(folders);
  return out;
}

export function rankFiles(
  query: string,
  files: { path: string; name: string }[],
  limit = 40,
): QuickOpenFileItem[] {
  if (!query.trim()) return [];
  return files
    .map((f) => {
      const pathScore = fuzzyScore(query, f.path);
      const nameScore = fuzzyScore(query, f.name);
      return { kind: "file" as const, path: f.path, name: f.name, score: Math.max(pathScore, nameScore) };
    })
    .filter((f) => f.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, limit);
}

export function rankChats(query: string, chats: ChatTab[], limit = 20): QuickOpenChatItem[] {
  if (!query.trim()) return [];
  return chats
    .map((c) => ({
      kind: "chat" as const,
      id: c.id,
      name: c.name,
      duckyStyle: c.duckyStyle,
      score: fuzzyScore(query, c.name),
    }))
    .filter((c) => c.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, limit);
}

export function recentTabs(openTabs: EditorTab[], limit = 12): EditorTab[] {
  return [...openTabs].reverse().slice(0, limit);
}

export function rankCommands(
  query: string,
  commands: { id: string; label: string }[],
  limit = 40,
): { id: string; label: string; score: number }[] {
  if (!query.trim()) {
    return commands.slice(0, limit).map((c) => ({ ...c, score: 0 }));
  }
  return commands
    .map((c) => ({ ...c, score: fuzzyScore(query, c.label) }))
    .filter((c) => c.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, limit);
}

export function rankSymbols(
  query: string,
  symbols: { name: string; kind: number; line: number; breadcrumb?: string }[],
  limit = 50,
): { name: string; kind: number; line: number; breadcrumb?: string; score: number }[] {
  if (!query.trim()) {
    return symbols.slice(0, limit).map((s) => ({ ...s, score: 0 }));
  }
  return symbols
    .map((s) => {
      const haystack = s.breadcrumb ? `${s.breadcrumb} ${s.name}` : s.name;
      return { ...s, score: fuzzyScore(query, haystack) };
    })
    .filter((s) => s.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, limit);
}
