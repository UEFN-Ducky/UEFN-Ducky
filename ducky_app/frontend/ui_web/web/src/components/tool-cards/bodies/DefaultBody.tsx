import { ToolPresenterView } from "../../tool-presenters/ToolPresenterView";
import type { ToolCardBodyProps } from "../toolCardTypes";
import { renderHighlightedPython } from "../highlightPython";

function isEmptyResultEnvelope(str: string): boolean {
  const t = str.trim();
  if (!t) return true;
  try {
    const parsed = JSON.parse(t) as unknown;
    if (parsed === null || parsed === "") return true;
    if (typeof parsed !== "object" || Array.isArray(parsed)) return false;
    const obj = parsed as Record<string, unknown>;
    const data = obj.data;
    const dataEmpty =
      data === undefined ||
      data === null ||
      data === "" ||
      (Array.isArray(data) && data.length === 0);
    const hintEmpty = !obj.hint || obj.hint === "";
    if (("ok" in obj || "data" in obj) && dataEmpty && hintEmpty) {
      const extra = Object.keys(obj).filter((k) => !["ok", "data", "hint", "tool", "error"].includes(k));
      return extra.length === 0;
    }
  } catch {
    return false;
  }
  return false;
}

/** Shared Arguments + Result + presenter used by generic/python/verse cards. */
export function DefaultBody({
  toolName,
  args,
  argsText,
  resultText,
  isSuccess,
  showResult = true,
  hideArgs = false,
  hint,
  onOpenFile,
}: ToolCardBodyProps) {
  // Prefer the raw script for editor Python tools instead of the JSON wrapper.
  const pythonCode =
    !hideArgs &&
    typeof args.code === "string" &&
    args.code.trim() &&
    (toolName === "execute_python" || toolName.includes("python"))
      ? args.code
      : null;

  const emptyResult = !resultText || isEmptyResultEnvelope(resultText);

  return (
    <>
      {!hideArgs ? (
        <div>
          <div className="tool-execution-card-section-label">
            {pythonCode ? "Code" : "Arguments"}
          </div>
          {pythonCode ? (
            <pre className="tool-execution-card-args-pre tool-execution-card-args-pre--python">
              <code className="tool-py-code">{renderHighlightedPython(pythonCode)}</code>
            </pre>
          ) : (
            <pre className="tool-execution-card-args-pre">{argsText || "{}"}</pre>
          )}
        </div>
      ) : null}

      {showResult ? (
        <div>
          <div className="tool-execution-card-section-label">Result</div>
          {emptyResult ? (
            <pre
              className={`tool-execution-card-result-pre${isSuccess ? " tool-execution-card-result-pre--success" : " tool-execution-card-result-pre--error"}`}
            >
              {isSuccess ? "(empty response)" : "No result body"}
            </pre>
          ) : (
            <ToolPresenterView
              toolName={toolName}
              arguments={args}
              resultText={resultText}
              isSuccess={isSuccess}
              onOpenFile={onOpenFile}
            />
          )}
          {hint ? <div className="tool-execution-card-hint">Hint: {hint}</div> : null}
        </div>
      ) : null}
    </>
  );
}
