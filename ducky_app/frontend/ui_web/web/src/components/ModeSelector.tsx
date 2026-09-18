import { useRef, useState } from "react";

import { Icons } from "../icons/Icons";
import type { AgentMode } from "../types/panel";
import { useMergedRef, useUiTarget } from "../ui-targets/registry";
import { DropdownPanel } from "./DropdownPanel";

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
  /** Spotlight id when this trigger is the chat composer control. */
  uiTarget?: string;
}

/** Icon trigger — Ask / Plan / Agent. Effort lives in the model picker. */
export function ModeSelector({
  activeMode,
  setMode,
  uiTarget = "",
}: ModeSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const anchorRef = useRef<HTMLButtonElement>(null);
  const uiTargetRef = useUiTarget(uiTarget, { kind: "dropdown", label: "Mode", route: "chat" });
  const triggerRef = useMergedRef(anchorRef, uiTargetRef);

  const currentMode = MODES.find((m) => m.id === activeMode) ?? MODES[0];
  const ModeIcon = MODE_ICON[currentMode.id];

  return (
    <div className="ui-relative mode-selector">
      <button
        ref={triggerRef}
        type="button"
        className={`mode-selector-btn${isOpen ? " is-open" : ""}`}
        data-mode={currentMode.id}
        title={currentMode.name}
        aria-label={currentMode.name}
        onClick={() => setIsOpen((v) => !v)}
      >
        <ModeIcon />
      </button>

      <DropdownPanel
        anchorRef={anchorRef}
        open={isOpen}
        onClose={() => setIsOpen(false)}
        placement="top"
        minWidth={110}
        width={110}
      >
        <div className="mode-selector-popup">
          <div className="mode-selector-popup-label">Mode</div>
          {MODES.map((m) => {
            const isSel = m.id === currentMode.id;
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
        </div>
      </DropdownPanel>
    </div>
  );
}
