/**
 * Which chats are blocked on the user: an agent's question or terminal command card
 * nobody has answered yet. Fed by the ask-user sessions and the terminal approvals, so
 * it clears the moment a card is answered in any window. Tabs, sidebar rows, group rows
 * and the header's background activity list show it instead of the running spinner.
 */
import { useMemo, useSyncExternalStore } from "react";

import { listAskUserSessions, subscribeAskUser } from "../ask-user/runAskUser";
import { requestPendingCard } from "../navigation/pendingCard";
import { requestOpenChatTab } from "../navigation/openChatReference";
import { listTerminalApprovals, subscribeTerminalApprovals } from "../terminal/terminalApprovals";

export type WaitingItem = {
  /** ask:<session id> or cmd:<request id>. */
  key: string;
  kind: "question" | "command";
  /** The chat that asked; empty when no chat did (answered from the header list). */
  convId: string;
  /** Other chats that show the same card (a group's hub shows its members' questions). */
  alsoIn: string[];
  /** The question, or the command. */
  text: string;
  /** Ask-user session id or terminal request id. */
  id: string;
};

/** A chat's oldest unanswered card: what it waits on and since when (ms epoch). */
export type ChatWait = { kind: WaitingItem["kind"]; at: number };

/** What a row shows: the card kind and how many seconds it has waited. */
export type NeedsYou = { kind: WaitingItem["kind"]; since: number };

type Snapshot = {
  items: WaitingItem[];
  /** Every chat with a card waiting (the asking chat and the hubs that show it). */
  chatIds: ReadonlySet<string>;
  /** Per chat id, its oldest card (same object until that changes, so rows don't re-render). */
  byChat: ReadonlyMap<string, ChatWait>;
};

const EMPTY: Snapshot = { items: [], chatIds: new Set(), byChat: new Map() };
let snapshot: Snapshot = EMPTY;
let signature = "";
const firstSeen = new Map<string, number>();
/** When each card was first seen (ms epoch). */
const firstSeenAt = new Map<string, number>();
const listeners = new Set<() => void>();
let stopSources: (() => void) | null = null;
let seq = 0;

function collect(): WaitingItem[] {
  const out: WaitingItem[] = [];
  for (const session of listAskUserSessions()) {
    out.push({
      key: `ask:${session.id}`,
      kind: "question",
      convId: session.convId,
      alsoIn: session.groupIds.filter((id) => id && id !== session.convId),
      text: session.questions[0]?.prompt || session.title || "Question",
      id: session.id,
    });
  }
  for (const item of listTerminalApprovals()) {
    out.push({
      key: `cmd:${item.request_id}`,
      kind: "command",
      convId: (item.conv_id || "").trim(),
      alsoIn: item.lead_conv_id && item.lead_conv_id !== item.conv_id ? [item.lead_conv_id] : [],
      text: item.command,
      id: item.request_id,
    });
  }
  return out;
}

function recompute(): void {
  const items = collect();
  for (const item of items) {
    if (firstSeen.has(item.key)) continue;
    firstSeen.set(item.key, ++seq);
    firstSeenAt.set(item.key, Date.now());
  }
  const live = new Set(items.map((item) => item.key));
  for (const key of [...firstSeen.keys()]) {
    if (live.has(key)) continue;
    firstSeen.delete(key);
    firstSeenAt.delete(key);
  }
  items.sort((a, b) => (firstSeen.get(a.key) ?? 0) - (firstSeen.get(b.key) ?? 0));
  const next = items.map((item) => `${item.key}@${item.convId}>${item.alsoIn.join(",")}`).join("|");
  if (next === signature) return;
  signature = next;
  const chatIds = new Set<string>();
  const byChat = new Map<string, ChatWait>();
  for (const item of items) {
    // Oldest first, so the first card per chat is the one it has waited on longest.
    for (const id of [item.convId, ...item.alsoIn]) {
      if (!id) continue;
      chatIds.add(id);
      if (byChat.has(id)) continue;
      const at = firstSeenAt.get(item.key) ?? Date.now();
      const prev = snapshot.byChat.get(id);
      byChat.set(id, prev && prev.kind === item.kind && prev.at === at ? prev : { kind: item.kind, at });
    }
  }
  snapshot = items.length ? { items, chatIds, byChat } : EMPTY;
  for (const cb of listeners) cb();
}

function ensureSources(): void {
  if (stopSources) return;
  const stopAsk = subscribeAskUser(recompute);
  const stopCmd = subscribeTerminalApprovals(recompute);
  stopSources = () => {
    stopAsk();
    stopCmd();
  };
  recompute();
}

export function subscribeWaitingChats(cb: () => void): () => void {
  ensureSources();
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

/** Oldest first. */
export function listWaitingItems(): WaitingItem[] {
  ensureSources();
  return snapshot.items;
}

export function waitingChatIds(): ReadonlySet<string> {
  ensureSources();
  return snapshot.chatIds;
}

export function isChatWaiting(convId: string | undefined | null): boolean {
  return Boolean(convId) && waitingChatIds().has(String(convId));
}

// Render-safe reads: the sources are wired up by subscribe, never during a render.
const readIds = (): ReadonlySet<string> => snapshot.chatIds;
const readItems = (): WaitingItem[] => snapshot.items;

export function useWaitingChatIds(): ReadonlySet<string> {
  return useSyncExternalStore(subscribeWaitingChats, readIds, readIds);
}

/** True while this chat has a card waiting (re-renders only when that flips). */
export function useChatWaiting(convId: string | undefined | null): boolean {
  const id = String(convId || "");
  const read = () => Boolean(id) && snapshot.chatIds.has(id);
  return useSyncExternalStore(subscribeWaitingChats, read, read);
}

export function useWaitingItems(): WaitingItem[] {
  return useSyncExternalStore(subscribeWaitingChats, readItems, readItems);
}

/**
 * "Needs you" for a row anywhere (sidebar, tabs, team bar): what the chat waits on and for how
 * many seconds, or null. Re-renders only when that chat's card changes; `since` is as of render.
 */
export function useNeedsYou(convId: string | undefined | null): NeedsYou | null {
  const id = String(convId || "");
  const read = () => (id ? snapshot.byChat.get(id) ?? null : null);
  const wait = useSyncExternalStore(subscribeWaitingChats, read, read);
  return useMemo(
    () => (wait ? { kind: wait.kind, since: Math.max(0, Math.round((Date.now() - wait.at) / 1000)) } : null),
    [wait],
  );
}

/** Open that chat and scroll to its unanswered card. */
export function openWaitingChat(convId: string, name = ""): boolean {
  const cid = (convId || "").trim();
  if (!cid) return false;
  requestPendingCard(cid);
  return requestOpenChatTab(cid, name);
}

/** Test helper. */
export function _resetWaitingChatsForTests(): void {
  stopSources?.();
  stopSources = null;
  snapshot = EMPTY;
  signature = "";
  firstSeen.clear();
  firstSeenAt.clear();
  listeners.clear();
}
