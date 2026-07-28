import { useCallback, useEffect, useState } from "react";
import { onApiReady } from "../hooks/onApiReady";
import { useConfirmModal } from "../contexts/ConfirmModalContext";
import type { GraphNode, PackSummary, StudioView } from "./model/graphTypes";
import { PackGridView } from "./components/PackGridView";
import { GraphEditorView } from "./components/GraphEditorView";
import * as api from "./api/skillPackStudioApi";
import { fileToBase64, pickSkillPackZipFile } from "./utils/fileTransfer";
import "./skill-pack-studio.css";

export function SkillPackStudio() {
  const { confirm } = useConfirmModal();
  const [packs, setPacks] = useState<PackSummary[]>([]);
  const [activePackId, setActivePackId] = useState<string | null>(null);
  const [packLabel, setPackLabel] = useState("");
  const [packKind, setPackKind] = useState("");
  const [nodes, setNodes] = useState<Record<string, GraphNode>>({});
  const [viewMode, setViewMode] = useState<StudioView>("packs");
  const [busy, setBusy] = useState(false);
  const [statusMsg, setStatusMsg] = useState("");

  const refreshPacks = useCallback(async () => {
    const list = await api.listPacks();
    setPacks(list);
  }, []);

  const loadPackGraph = useCallback(async (packId: string) => {
    setBusy(true);
    setStatusMsg("");
    try {
      const graph = await api.loadGraph(packId);
      setActivePackId(graph.packId);
      setPackLabel(graph.label);
      setPackKind(graph.kind);
      setNodes(graph.nodes);
      setViewMode("graph");
    } catch (e) {
      setStatusMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => onApiReady(() => void refreshPacks()), [refreshPacks]);

  const handleCreatePack = useCallback(
    async (id: string, label: string) => {
      setBusy(true);
      try {
        const packId = await api.createPack(id, label);
        await refreshPacks();
        await loadPackGraph(packId);
      } catch (e) {
        setStatusMsg(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [refreshPacks, loadPackGraph],
  );

  const handleImport = useCallback(async () => {
    setBusy(true);
    setStatusMsg("");
    try {
      const file = await pickSkillPackZipFile();
      if (!file) return;
      const dataBase64 = await fileToBase64(file);
      let res = await api.importPackBytes(file.name, dataBase64);
      if (res.conflict && res.existing_id) {
        const rename = await confirm({
          message: `Pack "${res.existing_id}" already exists. Import as a new id instead?`,
          confirmLabel: "Rename & import",
          cancelLabel: "Cancel",
        });
        if (!rename) return;
        const newId = `${res.existing_id}_imported`;
        res = await api.importPackBytes(file.name, dataBase64, newId, false);
      }
      if (res.cancelled) return;
      if (!res.ok) throw new Error(res.error ?? "Import failed");
      await refreshPacks();
      if (res.pack_id) await loadPackGraph(res.pack_id);
      setStatusMsg(`Imported ${res.label ?? res.pack_id}`);
    } catch (e) {
      setStatusMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [confirm, refreshPacks, loadPackGraph]);

  const handleBack = useCallback(() => {
    setViewMode("packs");
    setActivePackId(null);
    setStatusMsg("");
    void refreshPacks();
  }, [refreshPacks]);

  if (viewMode === "packs" || !activePackId) {
    return (
      <div>
        {statusMsg ? <div className="sps-status-msg">{statusMsg}</div> : null}
        <PackGridView
          packs={packs}
          busy={busy}
          onOpenPack={(id) => void loadPackGraph(id)}
          onCreatePack={(id, label) => void handleCreatePack(id, label)}
          onImport={() => void handleImport()}
        />
      </div>
    );
  }

  return (
    <div className="sps-root sps-root--graph">
      <GraphEditorView
        packId={activePackId}
        packLabel={packLabel}
        packKind={packKind}
        nodes={nodes}
        viewMode={viewMode === "schema" ? "schema" : "graph"}
        busy={busy}
        statusMsg={statusMsg}
        onBack={handleBack}
        onViewModeChange={setViewMode}
        onNodesChange={setNodes}
        onReload={() => void loadPackGraph(activePackId)}
        onStatus={setStatusMsg}
      />
    </div>
  );
}
