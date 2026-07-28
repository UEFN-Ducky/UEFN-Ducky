import { useRef, useState } from "react";
import { DropdownPanel } from "./DropdownPanel";
import type { AgentMode } from "../types/panel";

interface ModeSelectorProps {
  activeMode: AgentMode;
  setMode: (m: AgentMode) => void;
}

export function ModeSelector({ activeMode, setMode }: ModeSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const anchorRef = useRef<HTMLButtonElement>(null);

  const modes: { id: AgentMode; name: string; color: string }[] = [
    { id: "ask", name: "Ask", color: "var(--green)" },
    { id: "plan", name: "Plan", color: "var(--amber)" },
    { id: "agent", name: "Agent", color: "var(--red)" },
  ];

  const currentMode = modes.find((m) => m.id === activeMode) ?? modes[0];

  return (
    <div className="ui-relative">
      <button
        ref={anchorRef}
        type="button"
        className={`mode-selector-btn${isOpen ? " is-open" : ""}`}
        data-mode={currentMode.id}
        onClick={() => setIsOpen(!isOpen)}
      >
        {currentMode.name}
      </button>

      <DropdownPanel
        anchorRef={anchorRef}
        open={isOpen}
        onClose={() => setIsOpen(false)}
        placement="top"
        minWidth={110}
        width={110}
      >
        {modes.map((m) => {
          const isSel = m.id === activeMode;
          return (
            <div
              key={m.id}
              className={`mode-selector-option${isSel ? " is-selected" : ""}`}
              data-mode={m.id}
              onClick={() => {
                setMode(m.id);
                setIsOpen(false);
              }}
            >
              {m.name}
            </div>
          );
        })}
      </DropdownPanel>
    </div>
  );
}
