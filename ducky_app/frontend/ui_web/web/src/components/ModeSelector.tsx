import { useEffect, useRef, useState } from "react";

import { Icons } from "../icons/Icons";
import { getApi } from "../hooks/usePanelApi";
import type { AgentMode } from "../types/panel";
import { useMergedRef, useUiTarget } from "../ui-targets/registry";
import { DropdownPanel } from "./DropdownPanel";
import {
  EFFORT_OPTIONS,
  normalizeEffort,
  type ThinkingEffort,
} from "./EffortSelector";

const MODES: { id: AgentMode; name: string }[] = [
  { id: "ask", name: "Ask" },
  { id: "plan", name: "Plan" },
  { id: "agent", name: "Agent" },
];

const MODE_ICON: Record<AgentMode, () => JSX.Element> = {
  ask: Icons.Chat,
  plan: Icons.Plan,
  agent: Icons.Sparkles,
};

interface ModeSelectorProps {
  activeMode: AgentMode;
  setMode: (m: AgentMode) => void;
  showEffort?: boolean;
  convId?: string;
  effort?: string;
  onEffortChange?: (effort: ThinkingEffort) => void;
  /** Spotlight id when this trigger is the chat composer control. */
  uiTarget?: string;
}

/** Icon trigger — mode + optional reasoning in one popup. */
export function ModeSelector({
  activeMode,
  setMode,
  showEffort = false,
  convId,
  effort,
  onEffortChange,
  uiTarget = "",
}: ModeSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [effortValue, setEffortValue] = useState<ThinkingEffort>(() => normalizeEffort(effort));
  const anchorRef = useRef<HTMLButtonElement>(null);
  const uiTargetRef = useUiTarget(uiTarget, { kind: "dropdown", label: "Mode", route: "chat" });
  const triggerRef = useMergedRef(anchorRef, uiTargetRef);

  useEffect(() => {
    setEffortValue(normalizeEffort(effort));
  }, [effort, convId]);

  const currentMode = MODES.find((m) => m.id === activeMode) ?? MODES[0];
  const ModeIcon = MODE_ICON[currentMode.id];
  const currentEffort = EFFORT_OPTIONS.find((o) => o.id === effortValue) ?? EFFORT_OPTIONS[0];
  const title = showEffort
    ? `${currentMode.name} · Reasoning ${currentEffort.label}`
    : currentMode.name;

  const pickEffort = async (next: ThinkingEffort) => {
    setEffortValue(next);
    onEffortChange?.(next);
    if (!convId) return;
    try {
      await getApi()?.set_conversation_thinking_effort(convId, next);
    } catch {
      /* local state still updated */
    }
  };

  return (
    <div className="ui-relative mode-selector">
      <button
        ref={triggerRef}
        type="button"
        className={`mode-selector-btn${isOpen ? " is-open" : ""}`}
        data-mode={currentMode.id}
        title={title}
        aria-label={title}
        onClick={() => setIsOpen((v) => !v)}
      >
        <ModeIcon />
      </button>

      <DropdownPanel
        anchorRef={anchorRef}
        open={isOpen}
        onClose={() => setIsOpen(false)}
        placement="top"
        minWidth={showEffort ? 200 : 110}
        width={showEffort ? 220 : 110}
      >
        <div className="mode-selector-popup">
          <div className="mode-selector-popup-label">Mode</div>
          {MODES.map((m) => {
            const isSel = m.id === activeMode;
            return (
              <div
                key={m.id}
                className={`mode-selector-option${isSel ? " is-selected" : ""}`}
                data-mode={m.id}
                onClick={() => setMode(m.id)}
              >
                {m.name}
              </div>
            );
          })}
          {showEffort ? (
            <>
              <div className="mode-selector-popup-label">Reasoning</div>
              {EFFORT_OPTIONS.map((opt) => (
                <button
                  key={opt.id}
                  type="button"
                  className={`effort-selector-option${opt.id === effortValue ? " is-active" : ""}`}
                  onClick={() => void pickEffort(opt.id)}
                >
                  <span className="effort-selector-option-label">{opt.label}</span>
                  <span className="effort-selector-option-hint">{opt.hint}</span>
                </button>
              ))}
            </>
          ) : null}
        </div>
      </DropdownPanel>
    </div>
  );
}
