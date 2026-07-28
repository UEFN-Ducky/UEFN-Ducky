import { getApi } from "../../hooks/usePanelApi";
import { downloadBase64File } from "../utils/fileTransfer";
import type {
  SkillNodePatchDto,
  SkillPackDraftDto,
  SkillPackDraftResultDto,
  SkillPackImportResultDto,
  SkillPackManifestPatchDto,
  SubskillDraftResultDto,
  SubskillDto,
} from "../../types/panel";
import { runBridgeJob } from "../../hooks/bridgeJobAsync";
import type { PackWithFiles, SkillFile } from "../model/types";

function requireApi() {
  const api = getApi();
  if (!api) throw new Error("Panel API not ready");
  return api;
}

function fileFromDto(dto: SubskillDto): SkillFile {
  return {
    id: dto.id,
    file: dto.file ?? `${dto.id}.md`,
    title: dto.label,
    description: dto.description,
    loadCondition: dto.load_condition ?? "",
    content: dto.text ?? "",
    defaultEnabled: dto.default_enabled,
    alwaysOn: !!dto.always_on,
    origin: dto.origin ?? "",
  };
}

export interface PacksCatalog {
  packs: PackWithFiles[];
  defaultEnabledPacks: string[];
  defaultEnabledSubskills: Record<string, string[]>;
  error: string;
}

export async function listPacksWithFiles(): Promise<PacksCatalog> {
  const api = requireApi();
  const info = await api.get_skill_info();
  const packs = (info.packs ?? []).map((p) => ({
    id: p.id,
    label: p.label,
    description: p.description,
    kind: p.kind,
    version: p.version ?? 0,
    license: p.license ?? "",
    author: p.author ?? "",
    copyright: p.copyright ?? "",
    homepage: p.homepage ?? "",
    contact: p.contact ?? "",
    allowRedistribute:
      typeof p.allow_redistribute === "boolean" ? p.allow_redistribute : null,
    sourcePluginId: p.source_plugin_id ?? "",
    source: p.source ?? "",
    storeSlug: p.store_slug ?? "",
    origin: p.origin ?? "",
    // Catalog is metadata-only; markdown bodies load via loadPackFiles(packId).
    files: (p.subskills ?? []).map(fileFromDto),
  }));
  return {
    packs,
    defaultEnabledPacks: info.default_enabled_packs ?? [],
    defaultEnabledSubskills: info.default_enabled_subskills ?? {},
    error: info.error ?? "",
  };
}

/** Load markdown bodies for one pack (call when the user opens it). */
export async function loadPackFiles(packId: string): Promise<SkillFile[]> {
  const api = requireApi();
  if (typeof api.get_skill_pack_files !== "function") {
    throw new Error("get_skill_pack_files not available — restart the control panel");
  }
  const res = await api.get_skill_pack_files(packId);
  if (!res.ok) throw new Error(res.error ?? "Failed to load pack files");
  return (res.files ?? []).map((f) =>
    fileFromDto({
      id: f.id,
      label: f.label,
      description: f.description,
      default_enabled: f.default_enabled,
      always_on: f.always_on,
      load_condition: f.load_condition,
      file: f.file,
      text: f.text,
    }),
  );
}

export async function setPackDefaultEnabled(
  packId: string,
  enabled: boolean,
  currentPacks: string[],
  currentSubskills: Record<string, string[]>,
): Promise<{ packs: string[]; subskills: Record<string, string[]> }> {
  const api = requireApi();
  const nextPacks = enabled
    ? [...currentPacks, packId].filter((p, i, a) => a.indexOf(p) === i)
    : currentPacks.filter((p) => p !== packId);
  const res = await api.set_default_skill_selection(nextPacks, currentSubskills);
  return {
    packs: res.default_enabled_packs ?? res.enabled_packs ?? nextPacks,
    subskills: res.default_enabled_subskills ?? res.enabled_subskills ?? currentSubskills,
  };
}

export async function saveFileMeta(
  packId: string,
  fileId: string,
  patch: SkillNodePatchDto,
): Promise<void> {
  const api = requireApi();
  const res = await api.save_skill_node(packId, fileId, patch);
  if (!res.ok) throw new Error(res.error ?? "Save failed");
}

export async function savePackMeta(packId: string, patch: SkillPackManifestPatchDto): Promise<void> {
  const api = requireApi();
  if (typeof api.save_pack_manifest !== "function") {
    throw new Error("save_pack_manifest not available — restart the control panel");
  }
  const res = await api.save_pack_manifest(packId, patch);
  if (!res.ok) throw new Error(res.error ?? "Save pack failed");
}

export async function saveFileContent(packId: string, fileId: string, text: string): Promise<void> {
  const api = requireApi();
  await api.save_subskill(packId, fileId, text);
}

function slugify(label: string): string {
  const slug = label
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 48);
  return /^[a-z]/.test(slug) ? slug : `skill_${Date.now()}`;
}

export function packSlugFromLabel(label: string): string {
  return slugify(label);
}

export interface SkillPackDraft {
  label: string;
  description: string;
  slug: string;
  coreMarkdown: string;
  files: Array<{
    id: string;
    label: string;
    description: string;
    markdown: string;
  }>;
}

export function draftFromDto(dto: SkillPackDraftDto): SkillPackDraft {
  return {
    label: dto.label,
    description: dto.description,
    slug: packSlugFromLabel(dto.label),
    coreMarkdown: dto.core_markdown,
    files: (dto.files ?? []).map((f) => ({
      id: f.id,
      label: f.label,
      description: f.description,
      markdown: f.markdown,
    })),
  };
}

export function emptyDraftFromDescription(description: string, labelOverride?: string): SkillPackDraft {
  const firstLine = description.trim().split(/\n/)[0]?.trim() || "Custom Skill";
  const fromDesc = firstLine.length > 60 ? `${firstLine.slice(0, 57)}...` : firstLine;
  const label = (labelOverride?.trim() || fromDesc).slice(0, 60);
  return {
    label,
    description: description.trim() || label,
    slug: packSlugFromLabel(label),
    coreMarkdown: `# ${label}\n\n${description.trim() || "Add operator guidance for the agent here."}\n`,
    files: [],
  };
}

export interface ReferenceFileDraft {
  id: string;
  label: string;
  description: string;
  markdown: string;
}

export async function draftReferenceFile(
  packId: string,
  label: string,
  description: string,
  model: string,
  provider: string,
): Promise<ReferenceFileDraft> {
  requireApi();
  const res = await runBridgeJob<SubskillDraftResultDto>(
    "draft_subskill",
    [packId, label, description, model, provider],
    180_000,
  );
  if (!res.ok || !res.draft) throw new Error(res.error ?? "Generation failed");
  const d = res.draft;
  return {
    id: d.id,
    label: d.label,
    description: d.description,
    markdown: d.markdown,
  };
}

export async function createFileWithContent(
  packId: string,
  label: string,
  description: string,
  markdown: string,
  fileId?: string,
): Promise<string> {
  const id = fileId ?? slugify(label);
  const created = await createReferenceFile(packId, id, label, description);
  await saveFileContent(packId, created, markdown);
  return created;
}

export async function draftSkillPack(
  description: string,
  model: string,
  provider: string,
): Promise<SkillPackDraft> {
  const res = await runBridgeJob<SkillPackDraftResultDto>(
    "draft_skill_pack",
    [description, model, provider],
    180_000,
  );
  if (!res.ok || !res.draft) throw new Error(res.error ?? "Generation failed");
  return draftFromDto(res.draft);
}

async function createReferenceFile(
  packId: string,
  fileId: string,
  label: string,
  description: string,
): Promise<string> {
  const api = requireApi();
  const res = await api.create_subskill(packId, fileId, label, description, "", "");
  return res.subskill_id ?? fileId;
}

export async function commitSkillPackDraft(draft: SkillPackDraft): Promise<string> {
  const packId = await createPack(draft.slug, draft.label, draft.description);
  await saveFileContent(packId, "core", draft.coreMarkdown);
  for (const file of draft.files) {
    const fileId = await createReferenceFile(packId, file.id, file.label, file.description);
    await saveFileContent(packId, fileId, file.markdown);
  }
  return packId;
}

export async function createFile(packId: string, label: string): Promise<string> {
  const api = requireApi();
  const fileId = slugify(label);
  const res = await api.create_subskill(packId, fileId, label, "", "", "");
  return res.subskill_id ?? fileId;
}

export async function deleteFile(packId: string, fileId: string): Promise<void> {
  const api = requireApi();
  const res = await api.delete_subskill(packId, fileId);
  if (!res.ok) throw new Error(res.error ?? "Delete failed");
}

export async function createPack(packId: string, label: string, description?: string): Promise<string> {
  const api = requireApi();
  const res = await api.create_skill_pack(packId, label, description ?? "");
  return res.pack_id;
}

export async function deletePack(packId: string): Promise<void> {
  const api = requireApi();
  const res = await api.delete_skill_pack(packId);
  if (!res.ok) throw new Error((res as { error?: string }).error ?? "Delete pack failed");
}

export async function resetPack(packId: string): Promise<void> {
  const api = requireApi();
  await api.reset_skill_pack(packId);
}

export async function exportPack(packId: string) {
  const api = requireApi();
  const res = await api.export_skill_pack(packId);
  if (!res.ok) return res;
  if (!res.data_base64 || !res.filename) {
    return { ...res, ok: false, error: res.error ?? "Export produced no data" };
  }

  if (typeof api.prompt_save_skill_pack_export === "function") {
    const saved = await api.prompt_save_skill_pack_export(res.filename, res.data_base64);
    if (saved.cancelled) return { ok: false, cancelled: true };
    if (saved.ok && saved.path) return { ...res, path: saved.path };
    if (!saved.ok) return saved;
  }

  downloadBase64File(res.filename, res.data_base64);
  return res;
}

export async function importPackBytes(
  filename: string,
  dataBase64: string,
  packId?: string,
  replace?: boolean,
): Promise<SkillPackImportResultDto> {
  const api = requireApi();
  if (!api.import_skill_pack_bytes) {
    throw new Error("import_skill_pack_bytes not available — restart the control panel");
  }
  return api.import_skill_pack_bytes(filename, dataBase64, packId ?? "", replace ?? false);
}

export function openPackFolder(packId: string): void {
  void getApi()?.open_skill_pack_folder?.(packId);
}
