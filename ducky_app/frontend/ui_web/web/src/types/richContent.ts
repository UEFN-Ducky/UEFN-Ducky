export type RichHeadingLevel = 1 | 2 | 3 | 4;

export type RichCalloutTone = "info" | "warn" | "error" | "success";

export type RichInventoryKind =
  | "verse"
  | "devices"
  | "prop"
  | "blueprint"
  | "blender"
  | "umg"
  | "default";

export type RichProgramKey = "uefn" | "blender" | "verse" | "file";

export type RichInventoryItem = {
  kind: string;
  title: string;
  desc?: string;
  label?: string;
};

export type RichBlock =
  | { type: "heading"; level: RichHeadingLevel; text: string }
  | { type: "paragraph"; text: string }
  | { type: "list"; ordered?: boolean; items: string[] }
  | { type: "code"; language?: string; text: string }
  | { type: "accordion"; title: string; blocks: RichBlock[] }
  | { type: "table"; headers: string[]; rows: string[][] }
  | { type: "key_value"; pairs: { key: string; value: string }[] }
  | { type: "file_link"; path: string; label?: string }
  | { type: "callout"; tone: RichCalloutTone; text: string; title?: string }
  | { type: "header"; title: string; command?: string }
  | {
      type: "stats";
      changes?: number;
      blocked?: number;
      programs?: Partial<Record<RichProgramKey, number>>;
    }
  | { type: "inventory"; folder?: string; heading?: string; items: RichInventoryItem[] };

export interface RichEnvelope {
  __rich: true;
  blocks: RichBlock[];
  summary?: string;
}

export type ParsedRichContent =
  | { kind: "markdown"; text: string }
  | { kind: "blocks"; blocks: RichBlock[]; summary?: string };

export type OpenFileHandler = (
  path: string,
  name: string,
  options?: { line?: number },
) => void;
