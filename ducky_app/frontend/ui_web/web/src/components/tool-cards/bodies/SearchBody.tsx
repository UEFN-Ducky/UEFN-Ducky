import { Icons } from "../../../icons/Icons";
import { ToolPresenterView } from "../../tool-presenters/ToolPresenterView";
import { resolveToolPresenterBlocks } from "../../tool-presenters/resolveToolPresenter";
import type { ToolCardBodyProps } from "../toolCardTypes";

function isEffectivelyEmpty(str: string): boolean {
  const t = str.trim();
  if (!t || t === "{}" || t === "..." || t === "{\n}" || t === "null") return true;
  try {
    const parsed = JSON.parse(t) as unknown;
    if (parsed === null || parsed === "") return true;
    if (typeof parsed === "object" && !Array.isArray(parsed)) {
      const obj = parsed as Record<string, unknown>;
      const data = obj.data;
      const dataEmpty =
        data === undefined ||
        data === null ||
        data === "" ||
        (Array.isArray(data) && data.length === 0) ||
        (typeof data === "object" &&
          data !== null &&
          !Array.isArray(data) &&
          Object.keys(data as object).length === 0);
      const hintEmpty = obj.hint === undefined || obj.hint === null || obj.hint === "";
      if (("ok" in obj || "data" in obj) && dataEmpty && hintEmpty) {
        const extra = Object.keys(obj).filter(
          (k) => !["ok", "data", "hint", "tool", "error", "success"].includes(k),
        );
        if (extra.length === 0) return true;
      }
      if (Object.keys(obj).length === 0) return true;
    }
    if (Array.isArray(parsed) && parsed.length === 0) return true;
  } catch {
    /* plain text */
  }
  return false;
}

function queryFromArgs(args: Record<string, unknown>): string {
  for (const key of [
    "pattern",
    "search",
    "query",
    "q",
    "select",
    "directory",
    "path",
    "relative_path",
  ] as const) {
    const v = args[key];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return "";
}

function stripToolPrefix(name: string): string {
  return name
    .replace(/^mcp__uefn__/i, "")
    .replace(/^mcp__[^_]+__/i, "")
    .trim();
}

function parseSelectTools(raw: string): string[] {
  const trimmed = raw.trim();
  const body = trimmed.toLowerCase().startsWith("select:")
    ? trimmed.slice(trimmed.indexOf(":") + 1)
    : trimmed;
  return body
    .split(/[,|\n]/)
    .map((t) => stripToolPrefix(t.trim()))
    .filter(Boolean);
}

function looksLikeToolSelect(query: string, args: Record<string, unknown>): boolean {
  const q = query.trim().toLowerCase();
  if (q.startsWith("select:")) return true;
  if (typeof args.select === "string" && args.select.trim()) return true;
  // Comma-separated mcp__ tool names without the select: prefix
  if (/mcp__[\w]+__[\w]+/.test(query) && query.includes(",")) return true;
  return false;
}

function isToolRegistrySearch(toolName: string): boolean {
  const n = toolName.toLowerCase().replace(/-/g, "_");
  return (
    n === "toolsearch" ||
    n === "tool_search" ||
    n.includes("toolsearch") ||
    n.includes("tool_search")
  );
}

/** Search / scan / tool-registry body. */
export function SearchBody({
  toolName,
  args,
  resultText,
  isSuccess,
  isError,
  showResult = true,
  hint,
  onOpenFile,
}: ToolCardBodyProps) {
  const query = queryFromArgs(args);
  const registry = isToolRegistrySearch(toolName);
  const isSelect = looksLikeToolSelect(query, args);
  const selectedTools = isSelect
    ? parseSelectTools(
        typeof args.select === "string" && args.select.trim() ? args.select : query,
      )
    : [];

  const emptyResult = !resultText || isEffectivelyEmpty(resultText);
  const hasCustomPresenter =
    showResult &&
    !emptyResult &&
    resolveToolPresenterBlocks({
      toolName,
      arguments: args,
      resultText,
      isSuccess,
      onOpenFile,
    }) != null;

  // Tool-select UX: chips only — never dump select:… or empty JSON.
  if (isSelect) {
    return (
      <div className="tool-card-search-body tool-card-search-body--select">
        <div className="tool-card-select-header">
          <span className="tool-card-select-icon">
            <Icons.Search />
          </span>
          <div className="tool-card-select-header-text">
            <div className="tool-card-select-title">
              {selectedTools.length === 1
                ? "Selected 1 tool"
                : `Selected ${selectedTools.length} tools`}
            </div>
            <div className="tool-card-select-subtitle">Ready for this turn</div>
          </div>
        </div>
        {selectedTools.length > 0 ? (
          <ul className="tool-card-search-tool-list">
            {selectedTools.map((name) => (
              <li key={name} className="tool-card-search-tool-chip" title={name}>
                {name}
              </li>
            ))}
          </ul>
        ) : (
          <div className="tool-card-search-empty">No tools selected.</div>
        )}
        {hint ? <div className="tool-execution-card-hint">Hint: {hint}</div> : null}
      </div>
    );
  }

  const displayQuery = query || "(root directory)";

  return (
    <div className="tool-card-search-body">
      <div className="tool-card-search-block">
        <div className="tool-card-search-query">
          {registry ? <Icons.Search /> : <Icons.Folder />}
          <span>
            {registry ? "Searching tools for" : "Scanning for"}{" "}
            <strong title={displayQuery}>&quot;{displayQuery}&quot;</strong>…
          </span>
        </div>

        {showResult ? (
          <div
            className={`tool-card-search-result${isError ? " tool-card-search-result--error" : ""}`}
          >
            {emptyResult ? (
              <div className="tool-card-search-empty">
                {isSuccess ? "No matches found." : "Search failed."}
              </div>
            ) : hasCustomPresenter ? (
              <ToolPresenterView
                toolName={toolName}
                arguments={args}
                resultText={resultText}
                isSuccess={isSuccess}
                onOpenFile={onOpenFile}
              />
            ) : (
              <pre className="tool-card-search-result-pre">{resultText}</pre>
            )}
          </div>
        ) : null}
      </div>
      {hint ? <div className="tool-execution-card-hint">Hint: {hint}</div> : null}
    </div>
  );
}
