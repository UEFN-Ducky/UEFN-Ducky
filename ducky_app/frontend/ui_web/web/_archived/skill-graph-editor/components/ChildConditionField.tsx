interface ChildConditionFieldProps {
  value: string;
  onChange: (value: string) => void;
  onBlur: () => void;
}

export function ChildConditionField({ value, onChange, onBlur }: ChildConditionFieldProps) {
  return (
    <div className="sps-condition-field">
      <label className="sps-label sps-label--emerald">Load condition</label>
      <input
        className="sps-input sps-input--condition"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlur}
        placeholder="When should AI load this branch?"
      />
      <p className="sps-hint">AI loads this sub-skill only when this condition is met.</p>
    </div>
  );
}
