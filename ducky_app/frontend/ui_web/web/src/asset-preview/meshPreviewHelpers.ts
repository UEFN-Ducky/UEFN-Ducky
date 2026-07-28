import type { StaticMeshPreviewResult } from "../types/panel";

export type PreviewKind = "static_mesh" | "material" | "texture" | "niagara" | "other";

export function guessPreviewKind(relativePath: string, assetClass?: string): PreviewKind {
  let cls = (assetClass || "").trim().toLowerCase();
  // Redirectors are stubs for moved assets — ignore class and use path heuristics.
  if (cls === "objectredirector") cls = "";
  if (cls === "staticmesh") return "static_mesh";
  if (
    cls === "material" ||
    cls === "materialinstance" ||
    cls === "materialinstanceconstant" ||
    cls === "materialinstancedynamic"
  ) {
    return "material";
  }
  if (cls.startsWith("texture") || cls === "virtualtexture2d") return "texture";
  if (cls.includes("niagara") || cls === "particlesystem" || cls === "particle_system") {
    return "niagara";
  }

  const path = (relativePath || "").toLowerCase().replace(/\\/g, "/");
  const slash = path.lastIndexOf("/");
  const stem = (slash >= 0 ? path.slice(slash + 1) : path).replace(/\.uasset$/i, "");
  if (path.includes("/materials/") || stem.startsWith("m_") || stem.startsWith("mi_")) return "material";
  if (path.includes("/textures/") || path.includes("/texture/") || stem.startsWith("t_") || stem.startsWith("tex_")) {
    return "texture";
  }
  if (
    path.includes("/fx/") ||
    path.includes("/niagara") ||
    stem.startsWith("ns_") ||
    stem.startsWith("ne_") ||
    stem.startsWith("np_") ||
    stem.startsWith("fx_")
  ) {
    return "niagara";
  }
  if (stem.startsWith("sm_")) return "static_mesh";
  return "other";
}

/** Prefer path+class over a possibly-stale previewKind from a previous tab. */
function resolvedKind(
  relativePath: string,
  assetClass?: string,
  previewKind?: string,
): PreviewKind {
  const fromPath = guessPreviewKind(relativePath, assetClass);
  // Never trust a cached kind that disagrees with the current path/class.
  if (previewKind && previewKind === fromPath) return fromPath;
  if (assetClass) return fromPath;
  if (previewKind === "static_mesh" || previewKind === "material" || previewKind === "texture" || previewKind === "niagara") {
    // Only accept stale kind when path heuristics are inconclusive.
    if (fromPath === "other") return previewKind;
  }
  return fromPath;
}

export function canOfferStaticMeshPreview(
  relativePath: string,
  supportsMeshPreview?: boolean,
  assetClass?: string,
  previewKind?: string,
): boolean {
  const lower = (relativePath || "").toLowerCase().replace(/\\/g, "/");
  if (!lower.endsWith(".uasset")) return false;
  if (supportsMeshPreview === false) return false;
  return resolvedKind(relativePath, assetClass, previewKind) === "static_mesh";
}

export function canOfferMaterialPreview(
  relativePath: string,
  supportsMaterialPreview?: boolean,
  assetClass?: string,
  previewKind?: string,
): boolean {
  const lower = (relativePath || "").toLowerCase().replace(/\\/g, "/");
  if (!lower.endsWith(".uasset")) return false;
  if (supportsMaterialPreview === false) return false;
  return resolvedKind(relativePath, assetClass, previewKind) === "material";
}

export function canOfferTexturePreview(
  relativePath: string,
  supportsTexturePreview?: boolean,
  assetClass?: string,
  previewKind?: string,
): boolean {
  const lower = (relativePath || "").toLowerCase().replace(/\\/g, "/");
  if (!lower.endsWith(".uasset")) return false;
  if (supportsTexturePreview === false) return false;
  return resolvedKind(relativePath, assetClass, previewKind) === "texture";
}

export function cleanPreviewError(message: string | undefined | null): string {
  let text = (message || "").trim();
  if (!text) return "Preview failed";
  if (text.includes("Traceback")) text = text.split("Traceback")[0]!.trim();
  const lower = text.toLowerCase();
  const marker = "failed:";
  const idx = lower.lastIndexOf(marker);
  if (idx >= 0) text = text.slice(idx + marker.length).trim() || text;
  return text.slice(0, 240);
}

export function meshPreviewMediaFromResult(res: StaticMeshPreviewResult | null | undefined): {
  media_url: string;
  media_base_url: string;
  media_filename: string;
  mime: string;
} | null {
  if (!res?.ok || !res.media_url || !res.media_base_url || !res.media_filename) return null;
  return {
    media_url: res.media_url,
    media_base_url: res.media_base_url.endsWith("/") ? res.media_base_url : `${res.media_base_url}/`,
    media_filename: res.media_filename,
    mime: res.mime || "model/fbx",
  };
}
