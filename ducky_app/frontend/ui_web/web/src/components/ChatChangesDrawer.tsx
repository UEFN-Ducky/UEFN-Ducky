import { lazy, Suspense, useCallback, useEffect, useState } from "react";

import { Icons } from "../icons/Icons";
import { ScopedCss, useScopedClass } from "../utils/scopedCss";
import { useUiTarget } from "../ui-targets/registry";
import { ChatInputResizeHandle } from "./ChatInputResizeHandle";
import type { ChatTab } from "../types/panel";

const ChangesView = lazy(() => import("./changes/ChangesView").then((m) => ({ default: m.ChangesView })));

const MIN_H = 160;
const DEFAULT_H = 280;
const COMPOSER_FALLBACK = 140;

/** Survives ChatPane remounts (chat.id key + hidden tabs unmount). */
let ledgerOpen = false;
const ledgerOpenListeners = new Set<(open: boolean) => void>();

export function getLedgerOpen(): boolean {
  return ledgerOpen;
}

export function resetLedgerOpen(): void {
  ledgerOpen = false;
}

export function useLedgerOpen(): [boolean, (next: boolean | ((prev: boolean) => boolean)) => void] {
  const [open, setOpen] = useState(ledgerOpen);
  useEffect(() => {
    ledgerOpenListeners.add(setOpen);
    setOpen(ledgerOpen);
    return () => {
      ledgerOpenListeners.delete(setOpen);
    };
  }, []);
  const update = useCallback((next: boolean | ((prev: boolean) => boolean)) => {
    const value = typeof next === "function" ? next(ledgerOpen) : next;
    if (value === ledgerOpen) return;
    ledgerOpen = value;
    for (const cb of ledgerOpenListeners) cb(value);
  }, []);
  return [open, update];
}

/** Room above the composer chrome (textarea + toolbar + voice). Ledger lives inside the box. */
export function changesMaxHeight(host: HTMLElement | null): number {
  const pane = host;
  if (!pane) return MIN_H;
  const box = host.querySelector(".chat-pane-input-box") as HTMLElement | null;
  const ledger = host.querySelector(".chat-changes-host") as HTMLElement | null;
  if (box) {
    const chrome = Math.max(0, box.offsetHeight - (ledger?.offsetHeight || 0));
    return Math.max(MIN_H, pane.clientHeight - (chrome || COMPOSER_FALLBACK));
  }
  return Math.max(MIN_H, (host.clientHeight ?? 640) - COMPOSER_FALLBACK);
}

export function ChatChangesButton({
  open,
  onClick,
}: {
  open: boolean;
  onClick: () => void;
}) {
  const uiTargetRef = useUiTarget("chat.composer.changes", {
    kind: "button",
    label: "Ledger",
    route: "chat",
  });
  return (
    <button
      ref={uiTargetRef}
      type="button"
      className={`snip-btn chat-changes-btn${open ? " is-open" : ""}`}
      aria-pressed={open}
      aria-expanded={open}
      title="This chat's ledger — revert a turn"
      onClick={onClick}
    >
      <Icons.Clock />
    </button>
  );
}

interface ChatChangesSlideProps {
  open: boolean;
  convId: string;
  isGroup?: boolean;
  allChats?: ChatTab[];
  onOpenFile?: (path: string, name: string) => void;
  onOpenChat?: (chat: ChatTab) => void;
  /** Chat pane root: height cap and in-tab diff host. */
  host: HTMLElement | null;
  onClose?: () => void;
}

/** Inside the composer card, above the voice strip. Drag the top handle; tap toggles full / compact. */
export function ChatChangesSlide({
  open,
  convId,
  isGroup = false,
  allChats,
  onOpenFile,
  onOpenChat,
  host,
  onClose,
}: ChatChangesSlideProps) {
  const scopeClass = useScopedClass("chat-changes-host");
  const [height, setHeight] = useState(0);

  const maxHeight = useCallback(() => changesMaxHeight(host), [host]);

  useEffect(() => {
    if (!open) return;
    setHeight((cur) => cur || Math.min(DEFAULT_H, maxHeight()));
  }, [open, maxHeight]);

  if (!open) return null;

  const cap = maxHeight();
  const h = Math.min(height || Math.min(DEFAULT_H, cap), cap);
  const flush = h >= cap - 8;
  return (
    <div className={`chat-changes-host ${scopeClass}${flush ? " is-flush" : ""}`}>
      <ScopedCss selector={`.${scopeClass}`} rules={{ height: `${h}px` }} />
      <ChatInputResizeHandle
        label="Resize this chat's ledger"
        onDrag={(delta) => setHeight((cur) => Math.max(MIN_H, Math.min(cap, (cur || cap) + delta)))}
        onTap={() => setHeight((cur) => ((cur || cap) >= cap - 8 ? DEFAULT_H : cap))}
      />
      {onClose ? (
        <button
          type="button"
          className="chat-changes-close"
          title="Close ledger"
          aria-label="Close ledger"
          onClick={onClose}
        >
          <Icons.Close />
        </button>
      ) : null}
      <Suspense fallback={<p className="changes-empty">Loading ledger…</p>}>
        <ChangesView
          convId={isGroup ? "" : convId}
          groupId={isGroup ? convId : ""}
          hideDuckyFilter={!isGroup}
          allChats={allChats}
          onOpenFile={onOpenFile}
          onOpenChat={onOpenChat}
          modalContainer={host}
        />
      </Suspense>
    </div>
  );
}
