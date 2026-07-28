/**
 * Mount core ChoiceDropdown into a DOM node for shell.boot plugins
 * (vanilla JS cannot import React components directly).
 */
import { useEffect, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ChoiceDropdown, type ChoiceOption } from "../components/ChoiceDropdown";

export type MountChoiceDropdownOpts = {
  mode?: "radio" | "checkbox";
  value?: string;
  values?: string[];
  options: ChoiceOption[];
  onChange: (next: string | string[]) => void;
  "aria-label"?: string;
  size?: "default" | "compact";
  placeholder?: string;
  className?: string;
  disabled?: boolean;
};

export type MountedChoiceDropdown = {
  setValue: (value: string) => void;
  setValues: (values: string[]) => void;
  unmount: () => void;
};

type Setters = {
  setValue?: (v: string) => void;
  setValues?: (v: string[]) => void;
};

function RadioMount({
  initial,
  options,
  onChange,
  ariaLabel,
  size,
  placeholder,
  className,
  disabled,
  setters,
}: {
  initial: string;
  options: ChoiceOption[];
  onChange: (v: string) => void;
  ariaLabel?: string;
  size?: "default" | "compact";
  placeholder?: string;
  className?: string;
  disabled?: boolean;
  setters: Setters;
}) {
  const [value, setValue] = useState(initial);
  useEffect(() => {
    setters.setValue = setValue;
    return () => {
      delete setters.setValue;
    };
  }, [setters]);
  return (
    <ChoiceDropdown
      mode="radio"
      value={value}
      options={options}
      size={size}
      placeholder={placeholder}
      className={className}
      disabled={disabled}
      aria-label={ariaLabel}
      onChange={(next) => {
        setValue(next);
        onChange(next);
      }}
    />
  );
}

function CheckboxMount({
  initial,
  options,
  onChange,
  ariaLabel,
  size,
  placeholder,
  className,
  disabled,
  setters,
}: {
  initial: string[];
  options: ChoiceOption[];
  onChange: (v: string[]) => void;
  ariaLabel?: string;
  size?: "default" | "compact";
  placeholder?: string;
  className?: string;
  disabled?: boolean;
  setters: Setters;
}) {
  const [values, setValues] = useState(initial);
  useEffect(() => {
    setters.setValues = setValues;
    return () => {
      delete setters.setValues;
    };
  }, [setters]);
  return (
    <ChoiceDropdown
      mode="checkbox"
      values={values}
      options={options}
      size={size}
      placeholder={placeholder}
      className={className}
      disabled={disabled}
      aria-label={ariaLabel}
      onChange={(next) => {
        setValues(next);
        onChange(next);
      }}
    />
  );
}

/** Imperative mount for shell.boot — returns setters + unmount. */
export function mountChoiceDropdown(
  container: HTMLElement,
  opts: MountChoiceDropdownOpts,
): MountedChoiceDropdown {
  const root: Root = createRoot(container);
  const setters: Setters = {};
  const ariaLabel = opts["aria-label"];

  if (opts.mode === "checkbox") {
    root.render(
      <CheckboxMount
        initial={Array.isArray(opts.values) ? opts.values : []}
        options={opts.options}
        onChange={(v) => opts.onChange(v)}
        ariaLabel={ariaLabel}
        size={opts.size}
        placeholder={opts.placeholder}
        className={opts.className}
        disabled={opts.disabled}
        setters={setters}
      />,
    );
  } else {
    root.render(
      <RadioMount
        initial={typeof opts.value === "string" ? opts.value : ""}
        options={opts.options}
        onChange={(v) => opts.onChange(v)}
        ariaLabel={ariaLabel}
        size={opts.size}
        placeholder={opts.placeholder}
        className={opts.className}
        disabled={opts.disabled}
        setters={setters}
      />,
    );
  }

  return {
    setValue: (value) => setters.setValue?.(value),
    setValues: (values) => setters.setValues?.(values),
    unmount: () => root.unmount(),
  };
}
