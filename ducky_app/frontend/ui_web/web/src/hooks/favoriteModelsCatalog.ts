import type { CatalogModelRow } from "./modelsCatalogCache";
import type { CodingAgentDto } from "../types/panel";

export type FavoriteBackend = string;

export function isCodingAgentFavoriteId(
  id: string,
  codingAgents: CodingAgentDto[] = [],
): boolean {
  const key = id.trim().toLowerCase().replace(/-/g, "_");
  return codingAgents.some(
    (a) => String(a.id || "").trim().toLowerCase().replace(/-/g, "_") === key,
  );
}

export function qualifyFavorite(backend: string, modelId: string): string {
  return `${backend.trim()}:${modelId.trim()}`;
}

export function parseFavoriteSelection(
  raw: string,
  codingAgents: CodingAgentDto[] = [],
): { backend: string; modelId: string; qualified: string } | null {
  const text = (raw || "").trim();
  if (!text || !text.includes(":")) return null;
  const idx = text.indexOf(":");
  const backend = text.slice(0, idx).trim().toLowerCase().replace(/-/g, "_");
  let modelId = text.slice(idx + 1).trim();
  if (!backend || !modelId) return null;
  // Bare "default" is never a real model id — treat as "auto" (Cursor legacy).
  if (modelId.toLowerCase() === "default") {
    modelId = "auto";
  }
  // Opaque backend ids — accept any non-empty backend:model pair.
  // Coding-agent vs API is decided by live agent list when available.
  void codingAgents;
  return { backend, modelId, qualified: qualifyFavorite(backend, modelId) };
}

export function isLegacyAgentOnlyFavorite(
  raw: string,
  codingAgents: CodingAgentDto[] = [],
): boolean {
  return (
    isCodingAgentFavoriteId((raw || "").trim(), codingAgents) &&
    !(raw || "").includes(":")
  );
}

export function selectionNeedsRepick(
  raw: string,
  codingAgents: CodingAgentDto[] = [],
): boolean {
  const text = (raw || "").trim();
  if (!text) return false;
  if (isLegacyAgentOnlyFavorite(text, codingAgents)) return true;
  return parseFavoriteSelection(text, codingAgents) == null && text.includes(":");
}

function codingAgentLabel(id: string, codingAgents: CodingAgentDto[] = []): string {
  const key = id.trim().toLowerCase().replace(/-/g, "_");
  for (const agent of codingAgents) {
    if (String(agent.id || "").trim().toLowerCase().replace(/-/g, "_") === key) {
      const label = String(agent.label || "").trim();
      if (label) return label;
    }
  }
  return id.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function syntheticSavedRow(raw: string, codingAgents: CodingAgentDto[]): CatalogModelRow {
  const parsed = parseFavoriteSelection(raw, codingAgents);
  if (parsed) {
    const isAgent = isCodingAgentFavoriteId(parsed.backend, codingAgents);
    return {
      provider: isAgent ? codingAgentLabel(parsed.backend, codingAgents) : "Saved",
      providerKey: parsed.backend,
      id: parsed.qualified,
      name: `${parsed.modelId} (unavailable)`,
      supportsVision: false,
      supportsTools: true,
      supportsWebSearch: false,
      contextLimit: 0,
      priceIn: null,
      priceOut: null,
      isLocal: false,
    };
  }
  if (isLegacyAgentOnlyFavorite(raw, codingAgents)) {
    const id = raw.trim().toLowerCase().replace(/-/g, "_");
    return {
      provider: "Needs re-pick",
      providerKey: id,
      id: raw.trim(),
      name: `${codingAgentLabel(id, codingAgents)} (pick a model)`,
      supportsVision: false,
      supportsTools: true,
      supportsWebSearch: false,
      contextLimit: 0,
      priceIn: null,
      priceOut: null,
      isLocal: false,
    };
  }
  return {
    provider: "Saved",
    providerKey: "",
    id: raw.trim(),
    name: `${raw.trim()} (unavailable)`,
    supportsVision: false,
    supportsTools: true,
    supportsWebSearch: false,
    contextLimit: 0,
    priceIn: null,
    priceOut: null,
    isLocal: false,
  };
}

function isRicherRow(next: CatalogModelRow, prev: CatalogModelRow): boolean {
  return (
    next.contextLimit > prev.contextLimit ||
    next.priceIn != null ||
    next.priceOut != null ||
    next.supportsVision !== prev.supportsVision
  );
}

function qualifyApiRow(row: CatalogModelRow): CatalogModelRow {
  const backend = (row.providerKey || "").trim();
  if (!backend || row.id.includes(":")) return row;
  return { ...row, id: qualifyFavorite(backend, row.id) };
}

function codingAgentModelRows(agents: CodingAgentDto[]): CatalogModelRow[] {
  const out: CatalogModelRow[] = [];
  for (const agent of agents) {
    const aid = String(agent.id || "").trim();
    if (!aid) continue;
    if (!agent.enabled || !agent.available) continue;
    for (const model of agent.models || []) {
      let mid = String(model.id || "").trim();
      if (!mid) continue;
      if (mid.toLowerCase() === "default") {
        mid = "auto";
      }
      out.push({
        provider: agent.label || codingAgentLabel(aid, agents),
        providerKey: aid,
        id: qualifyFavorite(aid, mid),
        name: String(model.name || mid),
        supportsVision: false,
        supportsTools: true,
        supportsWebSearch: false,
        contextLimit: 0,
        priceIn: null,
        priceOut: null,
        isLocal: false,
      });
    }
  }
  return out;
}

/**
 * Live API catalog + live coding-agent models + synthetic rows for saved ids
 * that disappeared. Selection ids are always qualified (`backend:model`).
 */
export function buildFavoritePickerCatalog(
  savedSlotIds: string[],
  liveCatalog: CatalogModelRow[],
  codingAgents: CodingAgentDto[] = [],
): CatalogModelRow[] {
  const byId = new Map<string, CatalogModelRow>();

  for (const row of liveCatalog) {
    if (!row.supportsTools) continue;
    const qualified = qualifyApiRow(row);
    const prev = byId.get(qualified.id);
    if (!prev || isRicherRow(qualified, prev)) {
      byId.set(qualified.id, qualified);
    }
  }

  for (const row of codingAgentModelRows(codingAgents)) {
    const prev = byId.get(row.id);
    if (!prev || isRicherRow(row, prev)) {
      byId.set(row.id, row);
    }
  }

  for (const raw of savedSlotIds) {
    const id = raw.trim();
    if (!id) continue;
    if (!byId.has(id)) {
      byId.set(id, syntheticSavedRow(id, codingAgents));
    }
  }

  return Array.from(byId.values());
}
