export type RichRefKind =
  | "verse"
  | "file"
  | "asset"
  | "folder"
  | "actor"
  | "device"
  | "field"
  | "prefab"
  | "mesh"
  | "umg"
  | "keyword"
  | "tool"
  | "name";

export type RichRefOpen = { type: "file"; path: string } | { type: "asset"; path: string };

export type RichRef = {
  text: string;
  kind: RichRefKind;
  label: string;
  hint: string;
  open?: RichRefOpen;
};

const UMG_TYPES = /^(CanvasPanel|Image|TextBlock|Button|Overlay|StackBox|Border|SizeBox)$/;
const FILE_EXT = /\.(verse|versetest|vson|uasset|umap|py|json|md|txt|toml|cfg|blend|fbx)$/i;
const UEFN_PATH = /^\/[A-Za-z][\w]*(?:\/[\w./-]+)+$/;
const TOOLISH = /^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$/;
const ACTORISH = /^[A-Za-z][\w]*_[A-Za-z0-9]/;

function fileOpen(text: string): RichRefOpen {
  const p = text.replace(/\\/g, "/").replace(/^\.\//, "");
  if (/^[^/]+\.verse$/i.test(p)) return { type: "file", path: `Content/Verse/${p}` };
  if (/^verse\//i.test(p)) return { type: "file", path: p.toLowerCase().startsWith("content/") ? p : `Content/${p}` };
  if (p.toLowerCase().startsWith("content/")) return { type: "file", path: p };
  if (FILE_EXT.test(p) && !p.includes("/")) {
    if (/\.verse/i.test(p)) return { type: "file", path: `Content/Verse/${p}` };
    return { type: "file", path: `Content/${p}` };
  }
  if (!p.toLowerCase().startsWith("content/")) return { type: "file", path: `Content/${p.replace(/^\//, "")}` };
  return { type: "file", path: p };
}

function uefnToContent(path: string): string {
  const m = path.match(/^\/[^/]+\/(.+)$/);
  return (m?.[1] ?? path.replace(/^\//, "")).replace(/\/$/, "");
}

function withOpen(base: Omit<RichRef, "hint">, open?: RichRefOpen): RichRef {
  if (open?.type === "file") return { ...base, open, hint: "Open in the editor" };
  if (open?.type === "asset") return { ...base, open, hint: "Reveal in UEFN" };
  return { ...base, hint: "No live location — click copies the name" };
}

/** Classify a chat code chip so hover/click know what that token is. */
export function classifyRichRef(raw: string): RichRef {
  const text = raw.trim();
  if (!text || text.length > 120) {
    return { text: raw, kind: "name", label: "Name", hint: "No live location — click copies the name" };
  }
  if (text.startsWith("@") || text === "Props") {
    return withOpen({ text, kind: "keyword", label: text.startsWith("@") ? "Verse specifier" : "Editable field" });
  }
  if (UEFN_PATH.test(text)) {
    const rel = uefnToContent(text);
    const folder = !/\.\w+$/.test(rel);
    return withOpen(
      { text, kind: folder ? "folder" : "asset", label: folder ? "UEFN folder" : "UEFN asset" },
      { type: "asset", path: rel },
    );
  }
  if (FILE_EXT.test(text) || /^(?:content\/|verse\/)/i.test(text)) {
    const verse = /\.verse/i.test(text);
    return withOpen(
      { text, kind: verse ? "verse" : "file", label: verse ? "Verse file" : "Project file" },
      fileOpen(text),
    );
  }
  if (/^COL_/i.test(text)) return withOpen({ text, kind: "folder", label: "Blender collection" });
  if (/^SM_/i.test(text)) return withOpen({ text, kind: "mesh", label: "Static mesh" });
  if (/^(?:UW_|WBP_)/i.test(text) || UMG_TYPES.test(text)) {
    return withOpen({ text, kind: "umg", label: UMG_TYPES.test(text) ? "UMG type" : "UMG widget" });
  }
  if (/^(?:BP_|P_|PF_)/i.test(text)) return withOpen({ text, kind: "prefab", label: "Prefab / blueprint" });
  if (/_device$/i.test(text)) return withOpen({ text, kind: "device", label: "Creative device type" });
  if (TOOLISH.test(text)) return withOpen({ text, kind: "tool", label: "Tool" });
  if (ACTORISH.test(text)) return withOpen({ text, kind: "actor", label: "Level actor" });
  if (/^[A-Z][A-Za-z0-9]+$/.test(text)) return withOpen({ text, kind: "field", label: "Field / label" });
  return withOpen({ text, kind: "name", label: "Name" });
}
