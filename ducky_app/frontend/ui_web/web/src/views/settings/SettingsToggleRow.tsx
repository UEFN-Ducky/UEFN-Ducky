import type { ReactNode } from "react";

interface SettingsToggleRowProps {
  id: string;
  label: string;
  description?: ReactNode;
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}

export function SettingsToggleRow({
  id,
  label,
  description,
  checked,
  disabled,
  onChange,
}: SettingsToggleRowProps) {
  return (
    <div className="general-tab-toggle-row">
      <div className="general-tab-toggle-row-text">
        <label htmlFor={id} className="general-tab-toggle-label">
          {label}
        </label>
        {description ? <p className="general-tab-toggle-desc">{description}</p> : null}
      </div>
      <label className="general-tab-switch">
        <input
          type="checkbox"
          id={id}
          className="general-tab-switch-input"
          checked={checked}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span className="general-tab-switch-track" aria-hidden />
      </label>
    </div>
  );
}
