import { useCallback, useState } from "react";
import { handleDeepLink } from "../../navigation/deepLinks";
import type { PackWithFiles, SkillFile } from "../model/types";
import { fileBasename } from "../utils/fileDisplay";
import {
  isUserFile,
  packBadgeClass,
  packOriginBadge,
  storeListingSlug,
} from "../utils/packOrigin";
import { Icons } from "./icons";
import * as api from "../api/skillPackStudioApi";

interface PackEditorPaneProps {
  pack: PackWithFiles;
  busy: boolean;
  onPatchPack: (packId: string, patch: Partial<PackWithFiles>) => void;
  onSelectFile: (packId: string, fileId: string) => void;
  onStatus: (msg: string) => void;
}

type DetailTab = "content" | "details";

export function PackEditorPane({
  pack,
  busy: _busy,
  onPatchPack,
  onSelectFile,
  onStatus,
}: PackEditorPaneProps) {
  const [tab, setTab] = useState<DetailTab>("content");
  const metaLocked = pack.kind === "plugin" || pack.kind === "store";
  const originBadge = packOriginBadge(pack);
  const storeSlug = storeListingSlug(pack);

  const saveMeta = useCallback(
    async (next?: Partial<PackWithFiles>) => {
      if (pack.kind === "plugin" || pack.kind === "store") return;
      const merged = { ...pack, ...next };
      try {
        // Preserve existing frontmatter license fields; edit those via LICENSE / folder files.
        await api.savePackMeta(pack.id, {
          label: merged.label,
          description: merged.description,
          license: merged.license || null,
          author: merged.author || null,
          copyright: merged.copyright || null,
          homepage: merged.homepage || null,
          contact: merged.contact || null,
          allow_redistribute: merged.allowRedistribute ?? false,
        });
      } catch (e) {
        onStatus(e instanceof Error ? e.message : String(e));
      }
    },
    [pack, onStatus],
  );

  return (
    <main className="sps-file-main">
      <div className="sps-editor-header">
        <span className="sps-file-path">
          <Icons.Folder className="sps-icon-sm sps-icon-indigo" /> {pack.label}
          <span className={packBadgeClass(originBadge)}>{originBadge}</span>
        </span>
        {storeSlug ? (
          <span className="sps-editor-header-actions">
            <button
              type="button"
              className="sps-btn sps-btn--ghost sps-btn--compact"
              title="Open this item in Settings → Store"
              onClick={() => handleDeepLink(`uefn-ducky://store/${storeSlug}`)}
            >
              View in Store
            </button>
          </span>
        ) : null}
      </div>

      <div className="sps-tabs">
        <button
          type="button"
          className={tab === "content" ? "sps-tab is-active" : "sps-tab"}
          onClick={() => setTab("content")}
        >
          Content
        </button>
        <button
          type="button"
          className={tab === "details" ? "sps-tab is-active" : "sps-tab"}
          onClick={() => setTab("details")}
        >
          Details
        </button>
      </div>

      {tab === "content" ? (
        <div className="sps-pack-content-pane">
          <p className="sps-pack-blurb">{pack.description || "No description yet."}</p>
          <div className="sps-pack-inventory">
            {pack.files.map((f: SkillFile) => (
              <button
                key={f.id}
                type="button"
                className="sps-pack-inventory-item"
                onClick={() => onSelectFile(pack.id, f.id)}
              >
                <span className="sps-file-item-name">
                  <Icons.FileText className="sps-icon-sm" />
                  <span className="sps-file-item-basename">{fileBasename(f.file)}</span>
                  {isUserFile(f) ? (
                    <span className="sps-file-badge is-yours" title="You created this file">
                      Yours
                    </span>
                  ) : null}
                </span>
              </button>
            ))}
          </div>
          <p className="sps-hint">
            {pack.files.length} file{pack.files.length === 1 ? "" : "s"} — click one to edit its content.
          </p>
        </div>
      ) : (
        <div className="sps-details-pane">
          <div className="sps-field">
            <label className="sps-label">Pack id</label>
            <span className="sps-muted sps-mono">{pack.id}</span>
          </div>
          <div className="sps-field">
            <label className="sps-label">Version</label>
            <span className="sps-muted">{pack.version || 0}</span>
          </div>
          <div className="sps-field">
            <label className="sps-label">Kind</label>
            <span className={packBadgeClass(originBadge)}>{originBadge}</span>
          </div>
          {pack.sourcePluginId ? (
            <div className="sps-field">
              <label className="sps-label">Source plugin</label>
              <span className="sps-muted sps-mono">{pack.sourcePluginId}</span>
            </div>
          ) : null}
          {pack.storeSlug ? (
            <div className="sps-field">
              <label className="sps-label">Store listing</label>
              <span className="sps-muted sps-mono">{pack.storeSlug}</span>
            </div>
          ) : null}
          <div className="sps-field">
            <label className="sps-label">Pack label</label>
            {metaLocked ? (
              <span className="sps-muted">{pack.label}</span>
            ) : (
              <input
                className="sps-input"
                value={pack.label}
                onChange={(e) => onPatchPack(pack.id, { label: e.target.value })}
                onBlur={() => void saveMeta()}
              />
            )}
          </div>
          <div className="sps-field">
            <label className="sps-label">Description</label>
            {metaLocked ? (
              <p className="sps-muted">{pack.description || "No description."}</p>
            ) : (
              <>
                <textarea
                  className="sps-textarea sps-textarea--short"
                  value={pack.description}
                  onChange={(e) => onPatchPack(pack.id, { description: e.target.value })}
                  onBlur={() => void saveMeta()}
                />
                <p className="sps-hint">
                  Written to SKILL.md frontmatter — shown in the skill index for every ducky.
                </p>
              </>
            )}
          </div>
          <p className="sps-hint">
            License / author / copyright live in the pack folder (e.g. LICENSE.txt or SKILL.md
            frontmatter) — edit those files in Content, not here.
          </p>
        </div>
      )}
    </main>
  );
}
