/**
 * Reusable button dropdown — never use native <select>.
 * mode="radio" → single choice with radio markers
 * mode="checkbox" → multi choice with checkmarks (stays open until outside click)
 */

import { useId, useMemo, useRef, useState, type ReactNode } from "react";

import { Icons } from "../icons/Icons";
import { DropdownPanel } from "./DropdownPanel";

export type ChoiceOption = {
  value: string;
  label: string;
  hint?: string;
  disabled?: boolean;
  /** Optional group header (radio/checkbox lists). */
  group?: string;
};

type CommonProps = {
  options: ChoiceOption[];
  disabled?: boolean;
  placeholder?: string;
  className?: string;
  triggerClassName?: string;
  id?: string;
  "aria-label"?: string;
  placement?: "bottom" | "top";
  minWidth?: number;
  /** Compact trigger for filter bars / dense rows. */
  size?: "default" | "compact";
  emptyLabel?: string;
  /** Extra chrome above the option list (e.g. a speed slider). */
  header?: ReactNode;
  /** Extra chrome below the option list. */
  footer?: ReactNode;
};

type RadioProps = CommonProps & {
  mode?: "radio";
  value: string;
  onChange: (value: string) => void;
};

type CheckboxProps = CommonProps & {
  mode: "checkbox";
  values: string[];
  onChange: (values: string[]) => void;
};

export type ChoiceDropdownProps = RadioProps | CheckboxProps;

function groupOptions(options: ChoiceOption[]): { group: string | null; items: ChoiceOption[] }[] {
  const order: string[] = [];
  const map = new Map<string, ChoiceOption[]>();
  for (const opt of options) {
    const g = opt.group ?? "";
    if (!map.has(g)) {
      map.set(g, []);
      order.push(g);
    }
    map.get(g)!.push(opt);
  }
  return order.map((g) => ({ group: g || null, items: map.get(g)! }));
}

function isCheckbox(props: ChoiceDropdownProps): props is CheckboxProps {
  return props.mode === "checkbox";
}

export function ChoiceDropdown(props: ChoiceDropdownProps) {
  const {
    options,
    disabled,
    placeholder = "Select…",
    className,
    triggerClassName,
    id,
    placement = "bottom",
    minWidth = 200,
    size = "default",
    emptyLabel = "No options",
    header,
    footer,
  } = props;
  const ariaLabel = props["aria-label"];
  const checkbox = isCheckbox(props);
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLButtonElement>(null);
  const listId = useId();

  const selectedLabel = useMemo(() => {
    if (checkbox) {
      const vals = props.values;
      if (!vals.length) return placeholder;
      if (vals.length === 1) {
        return options.find((o) => o.value === vals[0])?.label ?? placeholder;
      }
      return `${vals.length} selected`;
    }
    const hit = options.find((o) => o.value === props.value);
    // Custom values (e.g. slider speed 1.37) fall back to placeholder.
    return hit?.label ?? placeholder;
  }, [checkbox, options, placeholder, props]);

  const selectedHint = useMemo(() => {
    if (checkbox) return undefined;
    return options.find((o) => o.value === props.value)?.hint;
  }, [checkbox, options, props]);

  const groups = useMemo(() => groupOptions(options), [options]);

  const toggleCheckbox = (value: string) => {
    if (!checkbox) return;
    const cur = props.values;
    const next = cur.includes(value) ? cur.filter((v) => v !== value) : [...cur, value];
    props.onChange(next);
  };

  return (
    <div className={`choice-dropdown${size === "compact" ? " choice-dropdown--compact" : ""}${className ? ` ${className}` : ""}`}>
      <button
        ref={anchorRef}
        id={id}
        type="button"
        className={`choice-dropdown-trigger${open ? " is-open" : ""}${triggerClassName ? ` ${triggerClassName}` : ""}`}
        disabled={disabled}
        aria-haspopup={checkbox ? "true" : "listbox"}
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={ariaLabel}
        onClick={() => {
          if (!disabled) setOpen((v) => !v);
        }}
      >
        <span className="choice-dropdown-trigger-copy">
          <span className="choice-dropdown-trigger-label">{selectedLabel}</span>
          {selectedHint ? <span className="choice-dropdown-trigger-hint">{selectedHint}</span> : null}
        </span>
        <span className={`choice-dropdown-chevron${open ? " is-open" : ""}`} aria-hidden>
          <Icons.ChevronDown />
        </span>
      </button>

      <DropdownPanel
        open={open}
        anchorRef={anchorRef}
        onClose={() => setOpen(false)}
        placement={placement}
        minWidth={minWidth}
        zIndex={100020}
      >
        <div
          id={listId}
          className="choice-dropdown-menu"
          role={checkbox ? "group" : "radiogroup"}
          aria-label={ariaLabel || "Options"}
        >
          {header ? <div className="choice-dropdown-header">{header}</div> : null}
          {options.length === 0 ? (
            <div className="choice-dropdown-empty">{emptyLabel}</div>
          ) : (
            groups.map(({ group, items }) => (
              <div key={group ?? "__ungrouped__"} className="choice-dropdown-group">
                {group ? <div className="choice-dropdown-group-label">{group}</div> : null}
                {items.map((opt) => {
                  const selected = checkbox
                    ? props.values.includes(opt.value)
                    : props.value === opt.value;
                  return (
                    <label
                      key={opt.value}
                      className={`choice-dropdown-option${selected ? " is-selected" : ""}${
                        opt.disabled ? " is-disabled" : ""
                      }`}
                    >
                      <input
                        type={checkbox ? "checkbox" : "radio"}
                        name={checkbox ? undefined : listId}
                        value={opt.value}
                        checked={selected}
                        disabled={opt.disabled || disabled}
                        onChange={() => {
                          if (opt.disabled) return;
                          if (checkbox) {
                            toggleCheckbox(opt.value);
                            return;
                          }
                          props.onChange(opt.value);
                          setOpen(false);
                        }}
                      />
                      {checkbox ? (
                        <span className="choice-dropdown-check" aria-hidden>
                          {selected ? <Icons.Check /> : null}
                        </span>
                      ) : (
                        <span className="choice-dropdown-radio" aria-hidden />
                      )}
                      <span className="choice-dropdown-option-copy">
                        <span className="choice-dropdown-option-label">{opt.label}</span>
                        {opt.hint ? (
                          <span className="choice-dropdown-option-hint">{opt.hint}</span>
                        ) : null}
                      </span>
                    </label>
                  );
                })}
              </div>
            ))
          )}
          {footer ? <div className="choice-dropdown-footer">{footer}</div> : null}
        </div>
      </DropdownPanel>
    </div>
  );
}
