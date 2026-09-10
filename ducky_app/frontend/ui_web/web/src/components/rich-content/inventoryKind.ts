import type { RichInventoryKind } from "../../types/richContent";

const KNOWN: readonly RichInventoryKind[] = [
  "verse",
  "devices",
  "prop",
  "blueprint",
  "blender",
  "umg",
  "default",
];

/** Map a free-form type label ("Verse device", "UMG Widget") to a kind class. */
export function inventoryKindFromLabel(label: string): RichInventoryKind {
  const t = label.trim().toLowerCase();
  if ((KNOWN as readonly string[]).includes(t) && t !== "default") return t as RichInventoryKind;
  if (t.includes("verse")) return "verse";
  if (t.includes("device")) return "devices";
  if (t.includes("blender")) return "blender";
  if (t.includes("umg") || t.includes("widget")) return "umg";
  if (t.includes("blueprint") || t.includes("prefab")) return "blueprint";
  if (t.includes("prop") || t.includes("clutter") || t.includes("mesh")) return "prop";
  return "default";
}
