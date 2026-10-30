import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { DropdownPanel } from "../components/DropdownPanel";
import type { PluginScopeChoice } from "../types/panel";
import { kindTitle } from "./scopeBarText";
import type { TeamScope } from "./teamScopeStore";

/** Above the Team modal's overlay. */
const PICKER_Z = 100020;

function choiceText(scope: TeamScope, choice: PluginScopeChoice): { name: string; sub: string; dot: string } {
  return choice.kind === "shared"
    ? { name: choice.label, sub: `${kindTitle(scope.kind)} copy`, dot: "shared" }
    : { name: "Local", sub: "Only on this PC", dot: "local" };
}

/** "Whose data the plugin shows": one button with the current choice, a dropdown of the rest. */
export function ScopePicker({ scope }: { scope: TeamScope }) {
  const { status, menu, refusal, shared, current, actions, busy } = scope;
  const anchor = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [focus, setFocus] = useState(0);
  const close = useCallback(() => setOpen(false), []);
  const choices = (menu || []).filter((choice) => !(refusal && !shared && choice.kind === "shared"));
  const picked = choices.find((choice) => choice.id === current);
  const shown = picked ? choiceText(scope, picked) : { name: status.scope?.label || "Local", sub: shared ? `${kindTitle(scope.kind)} copy` : "Only on this PC", dot: shared ? "shared" : "local" };
  const disabled = scope.switching || !status.canChange || menu === null;

  useEffect(() => {
    if (open) setFocus(Math.max(0, choices.findIndex((choice) => choice.id === current)));
    // Only when it opens: the menu object is new on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const pick = (choice: PluginScopeChoice) => {
    setOpen(false);
    if (choice.id !== current) void actions.choose(choice);
  };
  const onKey = (e: KeyboardEvent) => {
    if (!open) {
      if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        if (!disabled) setOpen(true);
      }
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : -1;
      setFocus((i) => (choices.length ? (i + step + choices.length) % choices.length : 0));
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (choices[focus]) pick(choices[focus]);
    }
  };

  const triggerText = !status.canChange ? "Open a project to choose" : menu === null ? "Loading…" : shown.name;
  return (
    <>
      <button ref={anchor} type="button" className="scope-picker" aria-haspopup="listbox" aria-expanded={open}
        aria-label={`Whose data: ${triggerText}`} disabled={disabled}
        onClick={() => setOpen((o) => !o)} onKeyDown={onKey}>
        <span className={`plugin-scope-dot plugin-scope-dot--${shown.dot}`} aria-hidden />
        <span className="plugin-scope-menu__text">
          <span className="plugin-scope-menu__name">{triggerText}</span>
          {status.canChange && menu !== null ? <span className="plugin-scope-menu__sub">{shown.sub}</span> : null}
        </span>
        <span className="scope-picker__chevron" aria-hidden>▾</span>
      </button>
      <DropdownPanel anchorRef={anchor} open={open} onClose={close} zIndex={PICKER_Z}>
        <div className="plugin-scope-menu scope-picker__list" role="listbox" aria-label="Whose data" onKeyDown={onKey}>
          {/* A folder that can't be shared from this PC: no team to offer, and why. */}
          {refusal && !shared ? <div className="plugin-scope-menu__note plugin-scope-menu__note--warn">{refusal}</div> : null}
          {choices.map((choice, i) => {
            const text = choiceText(scope, choice);
            return (
              <div key={choice.id} role="option" aria-selected={choice.id === current} tabIndex={-1}
                className={`plugin-scope-menu__row plugin-scope-menu__choice${i === focus ? " is-focused" : ""}`}
                onMouseEnter={() => setFocus(i)} onClick={() => pick(choice)}>
                <span className={`plugin-scope-dot plugin-scope-dot--${text.dot}`} aria-hidden />
                <span className="plugin-scope-menu__text">
                  <span className="plugin-scope-menu__name">{text.name}</span>
                  <span className="plugin-scope-menu__sub">{text.sub}</span>
                </span>
                {choice.id === current ? <span className="plugin-scope-menu__check" aria-hidden>✓</span> : null}
              </div>
            );
          })}
          {scope.offers.map((offer) => (
            <button key={offer.space} type="button" className="plugin-scope-btn plugin-scope-btn--block" disabled={busy}
              onClick={() => { setOpen(false); void actions.copyInto(offer); }}>
              Copy Local into {offer.label}
            </button>
          ))}
        </div>
      </DropdownPanel>
    </>
  );
}
