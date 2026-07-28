import { useEffect, useRef, useState } from "react";
import { DropdownPanel } from "./DropdownPanel";
import { getApi } from "../hooks/usePanelApi";

export type ThinkingEffort = "off" | "low" | "medium" | "high";

const OPTIONS: { id: ThinkingEffort; label: string; hint: string }[] = [
  { id: "off", label: "Off", hint: "No extended thinking" },
  { id: "low", label: "Low", hint: "~2k thinking tokens" },
  { id: "medium", label: "Med", hint: "~8k thinking tokens" },
  { id: "high", label: "High", hint: "~16k thinking tokens" },
];

function normalizeEffort(value: string | undefined | null): ThinkingEffort {
  const v = (value || "").trim().toLowerCase();
  if (v === "low" || v === "medium" || v === "high") return v;
  return "off";
}

interface EffortSelectorProps {
  convId: string;
  provider?: string;
  value?: string;
  onChange?: (effort: ThinkingEffort) => void;
}

/** Per-chat Anthropic extended-thinking effort control (hidden for non-Claude chats). */
export function EffortSelector({ convId, provider, value, onChange }: EffortSelectorProps) {
  const [effort, setEffort] = useState<ThinkingEffort>(() => normalizeEffort(value));
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    setEffort(normalizeEffort(value));
  }, [value, convId]);

  const show =
    !provider ||
    provider.toLowerCase().includes("anthropic") ||
    provider.toLowerCase() === "claude" ||
    provider.toLowerCase().startsWith("claude");
  // Also show for empty provider (default Anthropic) — Claude models are the common case.
  if (!show) return null;

  const current = OPTIONS.find((o) => o.id === effort) ?? OPTIONS[0];

  const pick = async (next: ThinkingEffort) => {
    setEffort(next);
    setOpen(false);
    onChange?.(next);
    const api = getApi();
    if (!api || !convId) return;
    try {
      await api.set_conversation_thinking_effort(convId, next);
    } catch {
      // ignore — local state still updated for this session
    }
  };

  return (
    <div className="effort-selector">
      <button
        ref={anchorRef}
        type="button"
        className="effort-selector-trigger"
        title={`Thinking effort: ${current.label}`}
        onClick={() => setOpen((v) => !v)}
      >
        Effort: {current.label}
      </button>
      <DropdownPanel open={open} anchorRef={anchorRef} onClose={() => setOpen(false)} placement="top">
        <div className="effort-selector-menu">
          {OPTIONS.map((opt) => (
            <button
              key={opt.id}
              type="button"
              className={`effort-selector-option${opt.id === effort ? " is-active" : ""}`}
              onClick={() => void pick(opt.id)}
            >
              <span className="effort-selector-option-label">{opt.label}</span>
              <span className="effort-selector-option-hint">{opt.hint}</span>
            </button>
          ))}
        </div>
      </DropdownPanel>
    </div>
  );
}
