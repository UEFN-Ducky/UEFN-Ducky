export type VerseTemplateKind = "builtin" | "custom" | "plugin";

export interface VerseTemplateFile {
  path: string;
  content: string;
}

export interface VerseTemplateMeta {
  id: string;
  name: string;
  icon: string;
  description?: string;
}

export interface VerseTemplate extends VerseTemplateMeta {
  kind: VerseTemplateKind;
  content: string;
  /** Set when kind === "plugin". */
  pluginId?: string;
  /** Multi-file pack: create this folder under the current tree parent. */
  folder?: string;
  /** Multi-file pack members (relative paths inside folder). */
  files?: VerseTemplateFile[];
  /** Which ?option slots this pack registers or consumes. */
  connects?: string[];
}

export interface VerseTemplateDto {
  id: string;
  name: string;
  icon: string;
  content: string;
  kind: "custom";
  folder?: string;
  files?: VerseTemplateFile[];
}
