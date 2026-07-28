import { getApi } from "../../hooks/usePanelApi";
import { downloadBase64File } from "../utils/fileTransfer";
import type {
  SkillNodePatchDto,
  SkillPackDto,
  SkillPackGraphDto,
  SkillPackImportResultDto,
} from "../../types/panel";
import type { GraphNode, PackGraph, PackSummary } from "../model/graphTypes";

function requireApi() {
  const api = getApi();
  if (!api) throw new Error("Panel API not ready");
  return api;
}

function graphFromDto(dto: SkillPackGraphDto): PackGraph {
  const nodes: Record<string, GraphNode> = {};
  for (const n of dto.nodes ?? []) {
    nodes[n.id] = {
      id: n.id,
      parentId: n.parent_id,
      type: "skill",
      title: n.label,
      description: n.description,
      loadCondition: n.load_condition ?? "",
      content: n.text ?? "",
      defaultEnabled: n.default_enabled,
      alwaysOn: n.always_on,
      x: n.x,
      y: n.y,
    };
  }
  return {
    packId: dto.pack_id,
    label: dto.label,
    description: dto.description,
    kind: dto.kind,
    nodes,
  };
}

export async function listPacks(): Promise<PackSummary[]> {
  const api = requireApi();
  const info = await api.get_skill_info();
  return (info.packs ?? []).map((p: SkillPackDto) => ({
    id: p.id,
    label: p.label,
    description: p.description,
    kind: p.kind,
  }));
}

export async function loadGraph(packId: string): Promise<PackGraph> {
  const api = requireApi();
  const dto = await api.get_skill_pack_graph(packId);
  if (!dto.ok) throw new Error(dto.error ?? "Failed to load graph");
  return graphFromDto(dto);
}

export async function saveNodeMeta(
  packId: string,
  nodeId: string,
  patch: SkillNodePatchDto,
): Promise<void> {
  const api = requireApi();
  const res = await api.save_skill_node(packId, nodeId, patch);
  if (!res.ok) throw new Error(res.error ?? "Save failed");
}

export async function saveNodeContent(packId: string, nodeId: string, text: string): Promise<void> {
  const api = requireApi();
  await api.save_subskill(packId, nodeId, text);
}

export async function saveLayout(
  packId: string,
  layout: Record<string, { x: number; y: number }>,
): Promise<void> {
  const api = requireApi();
  const res = await api.save_skill_layout(packId, layout);
  if (!res.ok) throw new Error(res.error ?? "Layout save failed");
}

export async function createChildNode(
  packId: string,
  parentId: string,
  fields: { label: string; description?: string; loadCondition?: string },
): Promise<string> {
  const api = requireApi();
  const subskillId = `skill_${Date.now()}`;
  await api.create_skill_node(
    packId,
    subskillId,
    fields.label,
    fields.description ?? "",
    parentId,
    fields.loadCondition ?? "",
  );
  return subskillId;
}

export async function deleteNode(packId: string, nodeId: string): Promise<void> {
  const api = requireApi();
  const res = await api.delete_subskill(packId, nodeId);
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
  if (!res.ok || res.cancelled) return res;
  if (!res.path && res.data_base64 && res.filename) {
    downloadBase64File(res.filename, res.data_base64);
  }
  return res;
}

export async function importPack(packId?: string, replace?: boolean): Promise<SkillPackImportResultDto> {
  const api = requireApi();
  return api.import_skill_pack(packId ?? "", replace ?? false);
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

export function buildLayoutFromNodes(
  nodes: Record<string, GraphNode>,
): Record<string, { x: number; y: number }> {
  const layout: Record<string, { x: number; y: number }> = {};
  for (const n of Object.values(nodes)) {
    layout[n.id] = { x: n.x, y: n.y };
  }
  return layout;
}
