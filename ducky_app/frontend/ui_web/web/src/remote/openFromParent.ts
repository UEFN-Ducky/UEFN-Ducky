/**
 * The phone page (uefnducky.org) asks Remote View to show one chat or workflow:
 * a tapped "agent finished" notification. It re-sends until this acks, because
 * the iframe's load event fires before React mounts this listener.
 */
import { isRemote } from "../hooks/usePanelApi";
import { subscribePanelPush } from "../hooks/usePanelPushBus";
import { requestFocusGraph } from "../hooks/graphActivity";
import { requestOpenChatTab } from "../navigation/openChatReference";
import { requestOpenWorkflowsTab } from "../navigation/openWorkflowsTab";
import { getFocusedChatForAsk, onFocusedChatChange } from "../ask-user/focusedChatForAsk";

export const UD_OPEN_TARGET = "ud-open-target";
export const UD_OPEN_TARGET_ACK = "ud-open-target-ack";
/** The chat on screen, so the phone page can reopen it after a reload. */
export const UD_CURRENT_TARGET = "ud-current-target";
const PARENT_ORIGIN = "https://uefnducky.org";
const ID = /^[A-Za-z0-9_.:-]{1,80}$/;

export type OpenTarget = { kind: "chat" | "workflow"; id: string };

export function parseOpenTarget(data: unknown): OpenTarget | null {
  if (!data || typeof data !== "object") return null;
  const d = data as { type?: unknown; kind?: unknown; id?: unknown };
  if (d.type !== UD_OPEN_TARGET) return null;
  const kind = d.kind === "chat" || d.kind === "workflow" ? d.kind : null;
  const id = typeof d.id === "string" ? d.id.trim() : "";
  return kind && ID.test(id) ? { kind, id } : null;
}

/** False while the chat list isn't up yet (nothing can open a tab). */
export function openTarget(target: OpenTarget): boolean {
  if (target.kind === "workflow") {
    requestFocusGraph(target.id);
    requestOpenWorkflowsTab();
    return true;
  }
  return requestOpenChatTab(target.id);
}

/** Open it now, or keep trying for 15 s while the chat list comes up. Returns a cancel. */
export function openTargetSoon(target: OpenTarget): () => void {
  if (openTarget(target)) return () => {};
  let tries = 0;
  const timer = window.setInterval(() => {
    if (openTarget(target) || ++tries >= 60) window.clearInterval(timer);
  }, 250);
  return () => window.clearInterval(timer);
}

export function installOpenFromParent(): () => void {
  if (!isRemote() || typeof window === "undefined" || window.parent === window) return () => {};
  let timer = 0;
  let last = "";
  const onMessage = (ev: MessageEvent) => {
    if (ev.origin !== PARENT_ORIGIN || ev.source !== window.parent) return;
    const target = parseOpenTarget(ev.data);
    if (!target) return;
    window.parent.postMessage({ type: UD_OPEN_TARGET_ACK, kind: target.kind, id: target.id }, PARENT_ORIGIN);
    const key = `${target.kind}:${target.id}`;
    // The phone re-sends until the ack lands; one open per target is enough.
    if (key === last && timer) return;
    last = key;
    window.clearInterval(timer);
    timer = 0;
    if (openTarget(target)) return;
    let tries = 0;
    timer = window.setInterval(() => {
      if (openTarget(target) || ++tries >= 60) {
        window.clearInterval(timer);
        timer = 0;
      }
    }, 250);
  };
  window.addEventListener("message", onMessage);
  const tellParent = (id: string) => {
    if (!ID.test(id)) return;
    window.parent.postMessage({ type: UD_CURRENT_TARGET, kind: "chat", id }, PARENT_ORIGIN);
  };
  const stopFocus = onFocusedChatChange(tellParent);
  tellParent(getFocusedChatForAsk());
  return () => {
    window.removeEventListener("message", onMessage);
    window.clearInterval(timer);
    stopFocus();
  };
}

/** A clicked Windows notification: the app pushes `open_target` to the main window. */
export function installOpenFromNotification(): () => void {
  if (isRemote()) return () => {};
  let cancel = () => {};
  const stop = subscribePanelPush((event) => {
    if (event.type !== "open_target") return;
    const target = parseOpenTarget({ type: UD_OPEN_TARGET, kind: event.kind, id: event.id });
    if (!target) return;
    cancel();
    cancel = openTargetSoon(target);
  });
  return () => {
    stop();
    cancel();
  };
}
