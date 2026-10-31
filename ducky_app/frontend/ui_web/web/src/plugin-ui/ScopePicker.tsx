import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { DropdownPanel } from "../components/DropdownPanel";
import type { PluginScopeChoice } from "../types/panel";
import { kindTitle } from "./scopeBarText";
import type { TeamScope } from "./teamScopeStore";

/** Above the Team modal's overlay. */
const PICKER_Z = 100020;

/** One row of a picker: its name, a second line and its dot colour (shared, local). */
export type PickerChoice = { id: string; name: string; sub: string; dot: string };

type ChoicePickerProps = {
  /** What it picks, for its name: "Whose data", "Team". */
  label: string;
  current: string;
  /** Null while they load. */
  choices: PickerChoice[] | null;
  /** The button's own text when nothing in `choices` is picked (or they're loading). */
  shown: PickerChoice;
  disabled?: boolean;
  onPick: (id: string) => void;
  className?: string;
  /** Lines above the choices (why a team isn't offered). */
  head?: ReactNode;
  /** Buttons under the choices; `close` shuts the dropdown. */
  foot?: (close: () => void) => ReactNode;
};

/** One button with the current choice and a dropdown of the rest: arrows and Enter pick. */
export function ChoicePicker({ label, current, choices, shown, disabled, onPick, className, head, foot }: ChoicePickerProps) {
  const anchor = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [focus, setFocus] = useState(0);
  const close = useCallback(() => setOpen(false), []);
  const list = choices ?? [];
  const picked = list.find((choice) => choice.id === current) ?? shown;

  useEffect(() => {
    if (open) setFocus(Math.max(0, list.findIndex((choice) => choice.id === current)));
    // Only when it opens: the list is new on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const pick = (choice: PickerChoice) => {
    setOpen(false);
    if (choice.id !== current) onPick(choice.id);
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
      setFocus((i) => (list.length ? (i + step + list.length) % list.length : 0));
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (list[focus]) pick(list[focus]);
    }
  };

  return (
    <>
      <button ref={anchor} type="button" className={`scope-picker${className ? ` ${className}` : ""}`} aria-haspopup="listbox"
        aria-expanded={open} aria-label={`${label}: ${picked.name}`} disabled={disabled}
        onClick={() => setOpen((o) => !o)} onKeyDown={onKey}>
        <span className={`plugin-scope-dot plugin-scope-dot--${picked.dot}`} aria-hidden />
        <span className="plugin-scope-menu__text">
          <span className="plugin-scope-menu__name">{picked.name}</span>
          {picked.sub ? <span className="plugin-scope-menu__sub">{picked.sub}</span> : null}
        </span>
        <span className="scope-picker__chevron" aria-hidden>▾</span>
      </button>
      <DropdownPanel anchorRef={anchor} open={open} onClose={close} zIndex={PICKER_Z}>
        <div className="plugin-scope-menu scope-picker__list" role="listbox" aria-label={label} onKeyDown={onKey}>
          {head}
          {list.map((choice, i) => (
            <div key={choice.id} role="option" aria-selected={choice.id === current} tabIndex={-1}
              className={`plugin-scope-menu__row plugin-scope-menu__choice${i === focus ? " is-focused" : ""}`}
              onMouseEnter={() => setFocus(i)} onClick={() => pick(choice)}>
              <span className={`plugin-scope-dot plugin-scope-dot--${choice.dot}`} aria-hidden />
              <span className="plugin-scope-menu__text">
                <span className="plugin-scope-menu__name">{choice.name}</span>
                <span className="plugin-scope-menu__sub">{choice.sub}</span>
              </span>
              {choice.id === current ? <span className="plugin-scope-menu__check" aria-hidden>✓</span> : null}
            </div>
          ))}
          {foot?.(close)}
        </div>
      </DropdownPanel>
    </>
  );
}

function choiceText(scope: TeamScope, choice: PluginScopeChoice): PickerChoice {
  return choice.kind === "shared"
    ? { id: choice.id, name: choice.label, sub: `${kindTitle(scope.kind)} copy`, dot: "shared" }
    : { id: choice.id, name: "Local", sub: "Only on this PC", dot: "local" };
}

/** "Whose data one plugin shows": that plugin's own copy, through its pane's callbacks. */
export function ScopePicker({ scope }: { scope: TeamScope }) {
  const { status, menu, refusal, shared, current, actions, busy } = scope;
  const choices = menu === null ? null
    : menu.filter((choice) => !(refusal && !shared && choice.kind === "shared")).map((choice) => choiceText(scope, choice));
  const name = !status.canChange ? "Open a project to choose" : menu === null ? "Loading…" : status.scope?.label || "Local";
  const sub = status.canChange && menu !== null ? (shared ? `${kindTitle(scope.kind)} copy` : "Only on this PC") : "";
  return (
    <ChoicePicker label="Whose data" current={current} choices={status.canChange ? choices : null}
      shown={{ id: current, name, sub, dot: shared ? "shared" : "local" }}
      disabled={scope.switching || !status.canChange || menu === null}
      onPick={(id) => {
        const choice = (menu || []).find((c) => c.id === id);
        if (choice) void actions.choose(choice);
      }}
      head={refusal && !shared ? <div className="plugin-scope-menu__note plugin-scope-menu__note--warn">{refusal}</div> : null}
      foot={(close) => scope.offers.map((offer) => (
        <button key={offer.space} type="button" className="plugin-scope-btn plugin-scope-btn--block" disabled={busy}
          onClick={() => { close(); void actions.copyInto(offer); }}>
          Copy Local into {offer.label}
        </button>
      ))} />
  );
}
