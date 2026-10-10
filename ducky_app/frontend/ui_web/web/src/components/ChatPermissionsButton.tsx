import { useCallback, useEffect, useRef, useState } from "react";

import { getApi } from "../hooks/usePanelApi";
import { Icons } from "../icons/Icons";
import type { ChatPermissionModeId, ChatPermissionsDto } from "../types/panel";
import { useMergedRef, useUiTarget } from "../ui-targets/registry";
import { announceChatPermissions, subscribeChatPermissions } from "./chatPermissionsSync";
import { DropdownPanel } from "./DropdownPanel";

interface ChatPermissionsButtonProps {
  convId: string;
  /** The agent the chat's next turn runs on (the composer's pick). */
  codingAgent: string;
}

/** Composer button: this chat's approval mode and the rules it allowed (like Claude Code's mode menu). */
export function ChatPermissionsButton({ convId, codingAgent }: ChatPermissionsButtonProps) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<ChatPermissionsDto | null>(null);
  const [error, setError] = useState("");
  const anchorRef = useRef<HTMLButtonElement>(null);
  const selfRef = useRef({});
  const uiTargetRef = useUiTarget("chat.composer.permissions", { kind: "dropdown", label: "Permissions", route: "chat" });
  const triggerRef = useMergedRef(anchorRef, uiTargetRef);

  const load = useCallback(async () => {
    const api = getApi();
    if (!api?.get_agent_permissions) return;
    try {
      const next = await api.get_agent_permissions(convId, codingAgent);
      if (next && Array.isArray(next.modes)) setState(next);
    } catch {
      /* the button keeps its last state */
    }
  }, [convId, codingAgent]);

  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => subscribeChatPermissions(convId, () => void load(), selfRef.current), [convId, load]);
  useEffect(() => {
    if (open) {
      setError("");
      void load();
    }
  }, [open, load]);

  const apply = useCallback(
    async (call: () => Promise<ChatPermissionsDto> | undefined) => {
      setError("");
      try {
        const next = await call();
        if (!next || !Array.isArray(next.modes)) return;
        setState(next);
        announceChatPermissions(convId, selfRef.current);
      } catch (err) {
        setError(err instanceof Error && err.message ? err.message : "Could not save the permission change.");
      }
    },
    [convId],
  );

  const pickMode = useCallback(
    (mode: ChatPermissionModeId) => {
      const row = state?.modes.find((m) => m.id === mode);
      if (!row?.available || mode === state?.mode) return;
      void apply(() => getApi()?.set_agent_permission_mode?.(convId, mode, codingAgent));
    },
    [state, apply, convId, codingAgent],
  );

  // Number keys pick a mode while the pop-up is open; Escape closes it.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        setOpen(false);
        return;
      }
      const index = Number(event.key) - 1;
      const row = state?.modes[index];
      if (!/^[1-9]$/.test(event.key) || !row) return;
      event.preventDefault();
      event.stopPropagation();
      pickMode(row.id);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, state, pickMode]);

  const title = state ? (state.asks ? `Permissions: ${state.label}` : `Permissions: ${state.agent_label} never asks`) : "Permissions";
  const rules = state?.rules ?? [];

  return (
    <div className="ui-relative chat-perm">
      <button
        ref={triggerRef}
        type="button"
        className={`chat-perm-btn${open ? " is-open" : ""}`}
        data-mode={state?.asks ? state.mode : "none"}
        title={title}
        aria-label={title}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <Icons.Shield />
      </button>
      <DropdownPanel anchorRef={anchorRef} open={open} onClose={() => setOpen(false)} placement="top" width={300} idealHeight={460}>
        <div className="chat-perm-menu" role="menu" aria-label="Permissions">
          <div className="chat-perm-caption">Permissions</div>
          {state && !state.asks ? <div className="chat-perm-note">{state.modes[0]?.reason}</div> : null}
          {state && state.asks && !state.own ? (
            <div className="chat-perm-note">Allow everything is on from {state.from_title}.</div>
          ) : null}
          {(state?.modes ?? []).map((mode, index) => {
            const active = mode.id === state?.mode;
            return (
              <button
                key={mode.id}
                type="button"
                role="menuitemradio"
                aria-checked={active}
                aria-label={mode.label}
                disabled={!mode.available}
                title={mode.available ? undefined : mode.reason}
                className={`chat-perm-item${active ? " is-active" : ""}`}
                onClick={() => pickMode(mode.id)}
              >
                <span className="chat-perm-key" aria-hidden>
                  {index + 1}
                </span>
                <span className="chat-perm-item-text">
                  <span className="chat-perm-item-title">{mode.label}</span>
                  <span className="chat-perm-item-sub">
                    {mode.available || !state?.asks ? mode.description : mode.reason}
                  </span>
                </span>
                <span className="chat-perm-check" aria-hidden>
                  {active ? <Icons.Check /> : null}
                </span>
              </button>
            );
          })}
          <div className="chat-perm-divider" role="separator" />
          <div className="chat-perm-caption">Allowed in this chat</div>
          {rules.length === 0 ? (
            <div className="chat-perm-empty">Nothing yet. "Always allow" on an approval card adds it here.</div>
          ) : (
            <ul className="chat-perm-rules" aria-label="Allowed in this chat">
              {rules.map((rule) => (
                <li key={rule.rule} className="chat-perm-rule">
                  <span className="chat-perm-rule-label" title={rule.label}>
                    {rule.label}
                  </span>
                  <button
                    type="button"
                    className="chat-perm-rule-remove"
                    aria-label={`Remove ${rule.label}`}
                    title="Remove"
                    onClick={() => void apply(() => getApi()?.remove_agent_permission_rule?.(convId, rule.rule, codingAgent))}
                  >
                    <Icons.Close />
                  </button>
                </li>
              ))}
            </ul>
          )}
          {rules.length > 1 ? (
            <button
              type="button"
              className="chat-perm-clear"
              onClick={() => void apply(() => getApi()?.clear_agent_permission_rules?.(convId, codingAgent))}
            >
              Clear all
            </button>
          ) : null}
          {error ? (
            <div className="chat-perm-error" role="alert">
              {error}
            </div>
          ) : null}
        </div>
      </DropdownPanel>
    </div>
  );
}
