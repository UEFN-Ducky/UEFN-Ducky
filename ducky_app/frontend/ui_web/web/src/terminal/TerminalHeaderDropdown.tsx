import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChoiceDropdown } from "../components/ChoiceDropdown";
import type { HeaderTerminalAction } from "../contexts/AppHeaderActionsContext";
import { Icons } from "../icons/Icons";
import type { TerminalShell } from "./types";

const MENU_WIDTH = 280;
const MENU_GAP = 6;

const SHELL_OPTIONS: { id: TerminalShell; label: string }[] = [
  { id: "bash", label: "bash" },
  { id: "powershell", label: "PowerShell" },
];

function runnerLabel(runner: "mcp" | "user" | null, running: boolean): string | null {
  if (!running) return null;
  if (runner === "mcp") return "MCP";
  if (runner === "user") return "you";
  return "running";
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

type TerminalHeaderDropdownProps = HeaderTerminalAction;

export function TerminalHeaderDropdown({
  terminals,
  defaultShell,
  onShellChange,
  onGotoTerminal,
  onCloseTerminal,
  onNewTerminal,
}: TerminalHeaderDropdownProps) {
  const [open, setOpen] = useState(false);
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const count = terminals.length;
  const title = count > 0 ? `${count} terminal${count === 1 ? "" : "s"}` : "Terminals";

  useLayoutEffect(() => {
    if (!open || !triggerRef.current) {
      setMenuPos(null);
      return;
    }
    const update = () => {
      const trigger = triggerRef.current;
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
      if (triggerRef.current?.contains(target)) return;
      if (menuRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const handleNew = () => {
    onNewTerminal();
    setOpen(false);
  };

  const menu =
    open && menuPos ? (
      <div
        ref={menuRef}
        className="terminal-header-menu terminal-header-menu--portaled no-drag"
        style={{ top: menuPos.top, left: menuPos.left }}
      >
        <div className="terminal-header-shell-row">
          <Icons.Terminal />
          <label className="terminal-header-shell-label">
            <span>Default shell</span>
            <ChoiceDropdown
              className="terminal-shell-select"
              size="compact"
              mode="radio"
              aria-label="Default shell"
              value={defaultShell}
              options={SHELL_OPTIONS.map((opt) => ({ value: opt.id, label: opt.label }))}
              onChange={(next) => onShellChange(next as TerminalShell)}
            />
          </label>
        </div>

        <div className="terminal-header-list">
          {terminals.length === 0 ? (
            <p className="terminal-header-empty">No terminals</p>
          ) : (
            terminals.map((term) => {
              const status = runnerLabel(term.runner, term.running);
              return (
                <div
                  key={term.id}
                  className={`terminal-header-item${term.active ? " is-active" : ""}${term.parked ? " is-parked" : ""}`}
                >
                  <button
                    type="button"
                    className="terminal-header-item-main"
                    onClick={() => {
                      onGotoTerminal(term.id);
                      setOpen(false);
                    }}
                  >
                    <span className="terminal-header-item-name">{term.name}</span>
                    <span className="terminal-header-item-meta">
                      <span className="terminal-header-item-shell">{term.shell}</span>
                      {term.parked ? (
                        <span className="terminal-header-item-status">hidden</span>
                      ) : null}
                      {status ? (
                        <span
                          className={`terminal-header-item-status${term.runner === "mcp" ? " is-mcp" : ""}${term.running ? " is-running" : ""}`}
                        >
                          {status}
                        </span>
                      ) : null}
                    </span>
                  </button>
                  <button
                    type="button"
                    className="terminal-header-item-close icon-btn no-drag"
                    title="Delete terminal (kills process)"
                    aria-label={`Delete ${term.name}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      onCloseTerminal(term.id);
                      setOpen(false);
                    }}
                  >
                    <Icons.Trash />
                  </button>
                </div>
              );
            })
          )}
        </div>

        <button type="button" className="terminal-header-new-btn" onClick={handleNew}>
          <Icons.Plus />
          <span>New terminal</span>
        </button>
      </div>
    ) : null;

  return (
    <div className="terminal-header-root">
      <button
        ref={triggerRef}
        type="button"
        className={`connection-status-btn terminal-header-trigger${open ? " is-active" : ""}${count > 0 ? " has-terminals" : ""}`}
        title={title}
        aria-label={title}
        onClick={() => setOpen(!open)}
      >
        <Icons.Terminal />
        {count > 0 ? <span className="terminal-header-badge">{count}</span> : null}
      </button>
      {menu ? createPortal(menu, document.body) : null}
    </div>
  );
}
