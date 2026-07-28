import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { useProblemsDuckyBridge } from "../contexts/ProblemsDuckyBridge";
import { Icons } from "../icons/Icons";
import type { ProblemsDraftPayload } from "../utils/formatProblemsDraft";
import { DuckyAvatar, DUCKY_AVATAR_SIZES } from "./ducky/DuckyAvatars";
import { TruncatedText } from "./TruncatedText";

const MENU_WIDTH = 260;
const MENU_GAP = 6;

interface VerseProblemsDuckyMenuProps {
  payload: ProblemsDraftPayload;
  disabled?: boolean;
  onSent?: () => void;
}

function computeMenuPosition(trigger: HTMLElement): { top: number; left: number } {
  const rect = trigger.getBoundingClientRect();
  let left = rect.right - MENU_WIDTH;
  if (left < 8) left = 8;
  if (left + MENU_WIDTH > window.innerWidth - 8) {
    left = Math.max(8, window.innerWidth - MENU_WIDTH - 8);
  }
  return { top: rect.bottom + MENU_GAP, left };
}

export function VerseProblemsDuckyMenu({ payload, disabled, onSent }: VerseProblemsDuckyMenuProps) {
  const { handlers } = useProblemsDuckyBridge();
  const [open, setOpen] = useState(false);
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const hasProblems = payload.errorCount + payload.warningCount > 0;
  const canUse = Boolean(handlers) && hasProblems && !disabled;

  useLayoutEffect(() => {
    if (!open || !buttonRef.current) {
      setMenuPos(null);
      return;
    }
    const update = () => {
      const trigger = buttonRef.current;
      if (!trigger) return;
      setMenuPos(computeMenuPosition(trigger));
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const target = e.target as Node;
      if (buttonRef.current?.contains(target)) return;
      if (panelRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open]);

  const sendTo = (chatId: string) => {
    handlers?.onSend(chatId, payload);
    setOpen(false);
    onSent?.();
  };

  const createAndSend = () => {
    void handlers?.onCreateAndSend(payload);
    setOpen(false);
    onSent?.();
  };

  const menu =
    open && handlers && menuPos ? (
      <div
        ref={panelRef}
        className="verse-problems-ducky-dropdown verse-problems-ducky-dropdown--portaled no-drag"
        style={{ top: menuPos.top, left: menuPos.left }}
      >
        <div className="verse-problems-ducky-dropdown-header">
          <span className="verse-problems-ducky-dropdown-title">Send to ducky</span>
        </div>
        {handlers.chats.length > 0 ? (
          <ul className="verse-problems-ducky-list">
            {handlers.chats.map((chat) => (
              <li key={chat.id}>
                <button type="button" className="verse-problems-ducky-item" onClick={() => sendTo(chat.id)}>
                  <DuckyAvatar
                    styleId={chat.duckyStyle}
                    size={DUCKY_AVATAR_SIZES.sidebar}
                    className="ducky-avatar--sidebar"
                  />
                  <TruncatedText className="verse-problems-ducky-item-label">{chat.name}</TruncatedText>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <div className="verse-problems-ducky-empty">No duckies yet</div>
        )}
        <div className="verse-problems-ducky-footer">
          <button type="button" className="verse-problems-ducky-create" onClick={createAndSend}>
            <Icons.Plus />
            <span>New ducky</span>
          </button>
        </div>
      </div>
    ) : null;

  return (
    <div className="verse-problems-ducky-wrap">
      <button
        ref={buttonRef}
        type="button"
        className={`icon-btn verse-problems-ducky-btn${open ? " is-active" : ""}`}
        title={canUse ? "Send problems to ducky" : "No problems to send"}
        aria-label="Send problems to ducky"
        aria-expanded={open}
        disabled={!canUse}
        onClick={(e) => {
          e.stopPropagation();
          if (!canUse) return;
          setOpen((v) => !v);
        }}
      >
        <Icons.Duck />
      </button>
      {menu ? createPortal(menu, document.body) : null}
    </div>
  );
}
