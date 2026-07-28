import { ChoiceDropdown, type ChoiceOption } from "../components/ChoiceDropdown";
import { clampSpeed, formatSpeed, snapSpeed, SPEED_OPTIONS } from "./voiceSettings";

export type SpeedDropdownProps = {
  id?: string;
  "aria-label"?: string;
  value: number;
  onChange: (value: number) => void;
  disabled?: boolean;
  size?: "default" | "compact";
  placement?: "bottom" | "top";
  minWidth?: number;
  /** Prepended options (e.g. Default speed = 0 for per-ducky override). */
  extraOptions?: ChoiceOption[];
};

/**
 * Talking-speed picker: radio presets + a 0.25–4× slider in the same popup.
 */
export function SpeedDropdown({
  id,
  value,
  onChange,
  disabled,
  size = "default",
  placement = "bottom",
  minWidth = 160,
  extraOptions,
  ...rest
}: SpeedDropdownProps) {
  const ariaLabel = rest["aria-label"] || "Talking speed";
  const strValue = String(value);
  const sliderValue = value > 0 ? clampSpeed(value) : 1;

  return (
    <ChoiceDropdown
      id={id}
      aria-label={ariaLabel}
      mode="radio"
      size={size}
      placement={placement}
      minWidth={minWidth}
      disabled={disabled}
      value={strValue}
      placeholder={value > 0 ? formatSpeed(value) : "Speed"}
      options={[...(extraOptions || []), ...SPEED_OPTIONS]}
      onChange={(next) => onChange(Number(next) || 0)}
      header={
        <div className="choice-speed-slider">
          <div className="choice-speed-slider-row">
            <span className="choice-speed-slider-label">Custom</span>
            <span className="choice-speed-slider-value">{formatSpeed(sliderValue)}</span>
          </div>
          <input
            type="range"
            className="choice-speed-slider-input"
            min={0.25}
            max={4}
            step={0.05}
            value={sliderValue}
            disabled={disabled}
            aria-label="Custom talking speed"
            onChange={(e) => onChange(snapSpeed(Number(e.target.value)))}
          />
          <div className="choice-speed-slider-ends" aria-hidden>
            <span>0.25×</span>
            <span>4×</span>
          </div>
        </div>
      }
    />
  );
}
