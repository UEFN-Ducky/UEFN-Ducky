import { useEffect, useRef } from "react";

import type { SlashCommand } from "./slashCommands";

interface SlashCommandMenuProps {
  commands: SlashCommand[];
  activeIndex: number;
  onHover: (index: number) => void;
  onSelect: (command: SlashCommand) => void;
}

/** Command palette above the composer, driven by the `/` prefix in the textarea. */
export function SlashCommandMenu({
  commands,
  activeIndex,
  onHover,
  onSelect,
}: SlashCommandMenuProps) {
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const active = listRef.current?.children[activeIndex];
    active?.scrollIntoView({ block: "nearest" });
  }, [activeIndex]);

  if (commands.length === 0) return null;

  return (
    <div className="slash-command-menu no-drag">
      <div className="slash-command-menu-header">Commands</div>
      <div ref={listRef} className="slash-command-menu-list">
        {commands.map((cmd, i) => (
          <button
            key={cmd.name}
            type="button"
            className={`slash-command-option${i === activeIndex ? " is-active" : ""}`}
            onMouseMove={() => onHover(i)}
            // Keep the textarea focused — blur would close the menu before the click lands.
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => onSelect(cmd)}
          >
            <span className="slash-command-option-name">
              /{cmd.name}
              {cmd.args ? <span className="slash-command-option-args"> {cmd.args}</span> : null}
            </span>
            <span className="slash-command-option-desc">{cmd.description}</span>
          </button>
        ))}
      </div>
      <div className="slash-command-menu-footer">
        <kbd>↑</kbd>
        <kbd>↓</kbd>
        <span>navigate</span>
        <kbd>Tab</kbd>
        <span>complete</span>
        <kbd>Esc</kbd>
        <span>dismiss</span>
      </div>
    </div>
  );
}
