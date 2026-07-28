import { useCallback, useEffect, useMemo, useState } from "react";

import { getApi } from "../../hooks/usePanelApi";
import { onApiReady } from "../../hooks/onApiReady";
import { usePluginContributions } from "../../hooks/usePluginContributions";
import { loadBuiltinTemplates } from "./loadBuiltinTemplates";
import type { VerseTemplate, VerseTemplateDto, VerseTemplateFile } from "./types";

export interface SaveCustomVerseTemplateInput {
  name: string;
  icon: string;
  content?: string;
  templateId?: string;
  folder?: string;
  files?: VerseTemplateFile[];
}

function dtoToTemplate(dto: VerseTemplateDto): VerseTemplate {
  const files = Array.isArray(dto.files)
    ? dto.files
        .filter(
          (f): f is VerseTemplateFile =>
            !!f && typeof f.path === "string" && typeof f.content === "string",
        )
        .map((f) => ({ path: f.path, content: f.content }))
    : undefined;
  return {
    id: dto.id,
    name: dto.name,
    icon: dto.icon,
    content: dto.content,
    kind: "custom",
    folder: dto.folder || undefined,
    files: files && files.length > 0 ? files : undefined,
  };
}

export function useVerseTemplates() {
  const builtin = useMemo(() => loadBuiltinTemplates(), []);
  const contrib = usePluginContributions();
  const [custom, setCustom] = useState<VerseTemplate[]>([]);
  const [loading, setLoading] = useState(true);

  const pluginTemplates = useMemo((): VerseTemplate[] => {
    return (contrib.verse_templates || []).map((t) => ({
      id: `plugin:${t.plugin_id}:${t.id}`,
      name: t.name,
      icon: t.icon,
      description: t.description,
      content: t.content,
      kind: "plugin" as const,
      pluginId: t.plugin_id,
      folder: t.folder,
      files: Array.isArray(t.files)
        ? t.files
            .filter(
              (f): f is { path: string; content: string } =>
                !!f && typeof f.path === "string" && typeof f.content === "string",
            )
            .map((f) => ({ path: f.path, content: f.content }))
        : undefined,
      connects: Array.isArray(t.connects)
        ? t.connects.filter((c): c is string => typeof c === "string" && c.length > 0)
        : undefined,
    }));
  }, [contrib.verse_templates]);

  const refresh = useCallback(async () => {
    const api = getApi();
    if (!api?.list_custom_verse_templates) {
      setCustom([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const rows = await api.list_custom_verse_templates();
      setCustom(rows.map(dtoToTemplate));
    } catch {
      setCustom([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => onApiReady(() => void refresh()), [refresh]);
  useEffect(() => {
    void refresh();
  }, [refresh]);

  const templates = useMemo(
    () => [...builtin, ...pluginTemplates, ...custom],
    [builtin, pluginTemplates, custom],
  );

  const saveCustom = useCallback(async (input: SaveCustomVerseTemplateInput) => {
    const api = getApi();
    if (!api?.save_custom_verse_template) {
      throw new Error("Custom templates unavailable");
    }
    const filesJson =
      input.files && input.files.length > 0 ? JSON.stringify(input.files) : "";
    const dto = await api.save_custom_verse_template(
      input.name,
      input.icon,
      input.content ?? "",
      input.templateId ?? "",
      input.folder ?? "",
      filesJson,
    );
    const next = dtoToTemplate(dto);
    setCustom((prev) => [...prev.filter((t) => t.id !== next.id), next]);
    return next;
  }, []);

  const deleteCustom = useCallback(async (templateId: string) => {
    const api = getApi();
    if (!api?.delete_custom_verse_template) return false;
    const result = await api.delete_custom_verse_template(templateId);
    if (result.ok) {
      setCustom((prev) => prev.filter((t) => t.id !== templateId));
    }
    return result.ok;
  }, []);

  return { templates, builtin, custom, pluginTemplates, loading, refresh, saveCustom, deleteCustom };
}
