import { Icons } from "../../../icons/Icons";
import type { ToolCardBodyProps } from "../toolCardTypes";

function isEffectivelyEmpty(str: string): boolean {
  const t = str.trim();
  return !t || t === "{}" || t === "..." || t === "{\n}";
}

/** Skill / knowledge-retrieval body: queried skill + prose answer. */
export function SkillBody({
  args,
  resultText,
  isError,
  showResult = true,
  hint,
}: ToolCardBodyProps) {
  const skill = typeof args.skill === "string" ? args.skill : "";
  const query =
    (typeof args.args === "string" && args.args) ||
    (typeof args.query === "string" && args.query) ||
    (typeof args.prompt === "string" && args.prompt) ||
    "";
  const title = skill ? `Queried Skill: ${skill}` : "General Documentation Query";

  return (
    <div className="tool-card-skill-body">
      <div className="tool-card-skill-header">
        <Icons.Sparkles />
        <span className="tool-card-skill-title">{title}</span>
        {query ? <span className="tool-card-skill-query">({query})</span> : null}
      </div>
      {showResult ? (
        <div
          className={`tool-card-skill-result${isError ? " tool-card-skill-result--error" : ""}`}
        >
          {isEffectivelyEmpty(resultText) ? "(empty response)" : resultText}
        </div>
      ) : null}
      {hint ? <div className="tool-execution-card-hint">Hint: {hint}</div> : null}
    </div>
  );
}
