/**
 * Which chats are blocked on the user: an agent's question or terminal command card
 * nobody has answered yet. Fed by the ask-user sessions and the terminal approvals, so
 * it clears the moment a card is answered in any window. Tabs, sidebar rows, group rows
 * and the header's background activity list show it instead of the running spinner.
 */
import { useSyncExternalStore } from "react";

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

type Snapshot = {
  items: WaitingItem[];
  /** Every chat with a card waiting (the asking chat and the hubs that show it). */
  chatIds: ReadonlySet<string>;
};

const EMPTY: Snapshot = { items: [], chatIds: new Set() };
let snapshot: Snapshot = EMPTY;
let signature = "";
const firstSeen = new Map<string, number>();
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
      alsoIn: [],
      text: item.command,
      id: item.request_id,
    });
  }
  return out;
}

function recompute(): void {
  const items = collect();
  for (const item of items) if (!firstSeen.has(item.key)) firstSeen.set(item.key, ++seq);
  const live = new Set(items.map((item) => item.key));
  for (const key of [...firstSeen.keys()]) if (!live.has(key)) firstSeen.delete(key);
  items.sort((a, b) => (firstSeen.get(a.key) ?? 0) - (firstSeen.get(b.key) ?? 0));
  const next = items.map((item) => `${item.key}@${item.convId}>${item.alsoIn.join(",")}`).join("|");
  if (next === signature) return;
  signature = next;
  const chatIds = new Set<string>();
  for (const item of items) {
    if (item.convId) chatIds.add(item.convId);
    for (const id of item.alsoIn) chatIds.add(id);
  }
  snapshot = items.length ? { items, chatIds } : EMPTY;
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
  listeners.clear();
}
