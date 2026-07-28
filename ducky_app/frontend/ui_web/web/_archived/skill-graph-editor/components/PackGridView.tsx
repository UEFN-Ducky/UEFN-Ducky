import { useCallback, useState } from "react";
import type { PackSummary } from "../model/graphTypes";
import { Icons } from "./icons";

interface PackGridViewProps {
  packs: PackSummary[];
  busy: boolean;
  onOpenPack: (packId: string) => void;
  onCreatePack: (id: string, label: string) => void;
  onImport: () => void;
}

export function PackGridView({ packs, busy, onOpenPack, onCreatePack, onImport }: PackGridViewProps) {
  const [showForm, setShowForm] = useState(false);
  const [packId, setPackId] = useState("");
  const [packLabel, setPackLabel] = useState("");

  const handleCreate = useCallback(() => {
    const id = packId.trim().toLowerCase().replace(/\s+/g, "_");
    const label = packLabel.trim() || id.replace(/_/g, " ");
    if (!id) return;
    onCreatePack(id, label);
    setShowForm(false);
    setPackId("");
    setPackLabel("");
  }, [onCreatePack, packId, packLabel]);

  return (
    <div className="sps-pack-grid-wrap">
      <div className="sps-pack-grid-header">
        <div>
          <h2 className="sps-title">Skill packs</h2>
          <p className="sps-subtitle">Select a pack to edit its skill tree.</p>
        </div>
        <div className="sps-pack-grid-actions">
          <button type="button" className="sps-btn sps-btn--ghost" disabled={busy} onClick={onImport}>
            <Icons.Upload className="sps-icon" /> Import
          </button>
          <button type="button" className="sps-btn sps-btn--primary" disabled={busy} onClick={() => setShowForm(true)}>
            <Icons.Plus className="sps-icon" /> Create pack
          </button>
        </div>
      </div>

      {showForm ? (
        <div className="sps-new-pack-form">
          <input
            className="sps-input"
            placeholder="pack_id"
            value={packId}
            onChange={(e) => setPackId(e.target.value)}
          />
          <input
            className="sps-input"
            placeholder="Display label"
            value={packLabel}
            onChange={(e) => setPackLabel(e.target.value)}
          />
          <button type="button" className="sps-btn sps-btn--primary" disabled={busy} onClick={handleCreate}>
            Create
          </button>
          <button type="button" className="sps-btn sps-btn--ghost" onClick={() => setShowForm(false)}>
            Cancel
          </button>
        </div>
      ) : null}

      <div className="sps-pack-grid">
        {packs.map((pack) => (
          <button
            key={pack.id}
            type="button"
            className="sps-pack-card"
            disabled={busy}
            onClick={() => onOpenPack(pack.id)}
          >
            <div className="sps-pack-card-icon">
              <Icons.Folder className="sps-icon-lg" />
            </div>
            <h3 className="sps-pack-card-title">{pack.label || pack.id}</h3>
            <p className="sps-pack-card-desc">{pack.description || "No description"}</p>
            <span className="sps-pack-card-kind">{pack.kind === "bundled" ? "Shipped" : "Custom"}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
