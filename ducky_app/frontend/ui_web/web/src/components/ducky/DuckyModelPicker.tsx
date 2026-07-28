import { useMemo, useRef, type ReactNode } from "react";

import {
  isCodingAgentFavoriteId,
  parseFavoriteSelection,
  qualifyFavorite,
} from "../../hooks/favoriteModelsCatalog";
import { getCachedModels } from "../../hooks/modelsCatalogCache";
import { codingAgentFromModel } from "./duckyProfileForm";
import { ModelSelector } from "../ModelSelector";

interface DuckyModelPickerProps {
  /** Qualified "backend:model" selection, or "" (no override). */
  model: string;
  onChange: (model: string) => void;
  /** Inline pill under the ducky name (profile detail layout). */
  variant?: "rows" | "chips";
  /** Selector text when nothing is picked. */
  placeholder?: string;
  /** Label in front of the selector. */
  label?: string;
  /** Help text under/after the picker. Empty string hides it. */
  hint?: string;
  /** Optional icon left of the selected model name (chips variant). */
  leadingIcon?: ReactNode;
  /** Show a "×" that resets the selection to "" (fall back to the default). */
  allowClear?: boolean;
  menuPlacement?: "top" | "bottom";
  /** Hide models that cannot run tools (default on for agent profiles). */
  requireTools?: boolean;
}

function normalizeAgentModelId(_agentId: string, modelId: string): string {
  const mid = (modelId || "").trim();
  return mid.toLowerCase() === "default" ? "auto" : mid;
}

/** Turn a ModelSelector pick into the qualified id settings/profiles store. */
export function qualifyModelPick(agent: string, modelId: string): string {
  const mid = normalizeAgentModelId(agent, (modelId || "").trim());
  if (!mid) return "";
  if (isCodingAgentFavoriteId(agent)) return qualifyFavorite(agent, mid);
  const bare = mid.includes(":") ? mid.slice(mid.indexOf(":") + 1) : mid;
  const row = (getCachedModels() ?? []).find((r) => r.id === bare || r.id === mid);
  const backend = (row?.providerKey || "").trim().toLowerCase().replace(/-/g, "_");
  if (!backend) return mid.includes(":") ? mid : "";
  return qualifyFavorite(backend, bare);
}

/**
 * Same Choose-agent ModelSelector as the chat composer. Stores qualified
 * `backend:model` ids (empty = use Settings → LLMs Default Model).
 */
export function DuckyModelPicker({
  model,
  onChange,
  variant = "rows",
  placeholder = "Default model",
  label = "Model",
  hint = "Optional — leave empty to use the Default Model from Settings → LLMs.",
  leadingIcon,
  allowClear = true,
  menuPlacement,
  requireTools = true,
}: DuckyModelPickerProps) {
  const value = (model || "").trim();
  const codingAgent = codingAgentFromModel(value);
  const selectedModel = useMemo(() => {
    const parsed = parseFavoriteSelection(value);
    if (parsed) return parsed.modelId;
    return value;
  }, [value]);

  const agentRef = useRef(codingAgent);
  agentRef.current = codingAgent;

  const setCodingAgent = (agent: string) => {
    agentRef.current = agent;
  };

  const setSelectedModel = (id: string) => {
    const next = (id || "").trim();
    if (!next) {
      onChange("");
      return;
    }
    onChange(qualifyModelPick(agentRef.current || "ducky", next));
  };

  const clearButton =
    allowClear && value ? (
      <button
        type="button"
        className="ducky-favorite-models-clear"
        title="Clear — use the Default Model from Settings → LLMs"
        aria-label="Clear model override"
        onClick={() => onChange("")}
      >
        ×
      </button>
    ) : null;

  const placement = menuPlacement ?? "bottom";

  const selector = (
    <ModelSelector
      selectedModel={selectedModel}
      setSelectedModel={setSelectedModel}
      codingAgent={codingAgent}
      setCodingAgent={setCodingAgent}
      preserveSelection
      requireTools={requireTools}
      placeholder={placeholder}
      menuPlacement={placement}
    />
  );

  if (variant === "chips") {
    return (
      <div className="ducky-favorite-models ducky-favorite-models--chips">
        <div className={`ducky-favorite-models-chip${value ? " is-selected" : ""}`}>
          <span className="ducky-favorite-models-chip-rank">{label}</span>
          {leadingIcon ? (
            <span className="ducky-favorite-models-chip-icon" aria-hidden>
              {leadingIcon}
            </span>
          ) : null}
          {selector}
          {clearButton}
        </div>
        {hint ? (
          <span className="ducky-editor-personality-hint ducky-favorite-models-hint">{hint}</span>
        ) : null}
      </div>
    );
  }

  return (
    <div className="ducky-favorite-models">
      <div className="ducky-favorite-models-row">
        {label ? <span className="ducky-favorite-models-slot">{label}</span> : null}
        {selector}
        {clearButton}
      </div>
      {hint ? <span className="ducky-editor-personality-hint">{hint}</span> : null}
    </div>
  );
}
