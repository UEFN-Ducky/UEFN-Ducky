interface DefaultEnabledToggleProps {
  checked: boolean;
  disabled?: boolean;
  alwaysOn?: boolean;
  isRoot?: boolean;
  onChange: (checked: boolean) => void;
}

export function DefaultEnabledToggle({
  checked,
  disabled,
  alwaysOn,
  isRoot = true,
  onChange,
}: DefaultEnabledToggleProps) {
  const isOn = checked || !!alwaysOn;

  return (
    <div className={`sps-default-toggle${isOn ? " is-on" : ""}`}>
      <label className="sps-default-toggle-label">
        <input
          type="checkbox"
          className="sps-checkbox"
          checked={isOn}
          disabled={disabled || alwaysOn}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span>Default enabled</span>
        {alwaysOn ? <span className="sps-muted"> (always on)</span> : null}
      </label>
      <p className="sps-hint">
        {isRoot
          ? "Ships on by default for new duckies. Node shows green on the graph."
          : "Enabled by default when the parent pack is active. Node shows green on the graph."}
      </p>
    </div>
  );
}
