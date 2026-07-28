import type { ToolCardBodyProps } from "../toolCardTypes";

function isEffectivelyEmpty(str: string): boolean {
  const t = str.trim();
  return !t || t === "{}" || t === "..." || t === "{\n}";
}

function commandFromArgs(args: Record<string, unknown>, argsText: string): string {
  const cmd = args.command ?? args.cmd ?? args.script;
  if (typeof cmd === "string" && cmd.trim()) return cmd.trim();
  if (!isEffectivelyEmpty(argsText) && argsText.trim() !== "{}") return argsText;
  return "(interactive session)";
}

/** Shell-style body: ❯ command + output pane. */
export function TerminalBody({
  args,
  argsText,
  resultText,
  isError,
  showResult = true,
  hint,
}: ToolCardBodyProps) {
  const command = commandFromArgs(args, argsText);
  const output = isEffectivelyEmpty(resultText) ? "(no output)" : resultText;

  return (
    <div className="tool-card-terminal-body">
      <div className="tool-card-terminal-block">
        <div className="tool-card-terminal-command">
          <span className="tool-card-terminal-prompt" aria-hidden>
            ❯
          </span>
          <span className="tool-card-terminal-command-text">{command}</span>
        </div>
        {showResult ? (
          <div
            className={`tool-card-terminal-output${isError ? " tool-card-terminal-output--error" : ""}`}
          >
            {output}
          </div>
        ) : null}
      </div>
      {hint ? <div className="tool-execution-card-hint">Hint: {hint}</div> : null}
    </div>
  );
}
