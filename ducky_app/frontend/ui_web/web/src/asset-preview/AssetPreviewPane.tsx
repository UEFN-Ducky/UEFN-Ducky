import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { CtrlWheelZoomRoot } from "../components/CtrlWheelZoomRoot";
import { ModelFilePane } from "../components/ModelFilePane";
import { onApiReady } from "../hooks/onApiReady";
import type { StaticMeshPreviewResult } from "../types/panel";
import { useVerseEditorOptional } from "../verse-editor";
import { basename } from "../verse-editor/utils/isVerseFile";

import "./asset-preview.css";
import {
  canOfferMaterialPreview,
  canOfferStaticMeshPreview,
  canOfferTexturePreview,
  cleanPreviewError,
  guessPreviewKind,
  meshPreviewMediaFromResult,
} from "./meshPreviewHelpers";
import { useAssetPreview } from "./useAssetPreview";

interface AssetPreviewPaneProps {
  relativePath: string;
}

function MetaRow({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <div className="asset-preview-meta">
      <strong>{label}:</strong> {value}
    </div>
  );
}

export function AssetPreviewPane({ relativePath }: AssetPreviewPaneProps) {
  const verseEditor = useVerseEditorOptional();
  const { result, loading, error } = useAssetPreview(relativePath);
  const [hexContent, setHexContent] = useState<string | null>(null);
  const [hexOpen, setHexOpen] = useState(false);
  const [openError, setOpenError] = useState<string | null>(null);
  const [meshLoading, setMeshLoading] = useState(false);
  const [meshError, setMeshError] = useState<string | null>(null);
  const [meshPreview, setMeshPreview] = useState<StaticMeshPreviewResult | null>(null);
  const [materialLoading, setMaterialLoading] = useState(false);
  const [materialPreviewUrl, setMaterialPreviewUrl] = useState<string | null>(null);
  const meshRequestRef = useRef(0);

  useEffect(() => {
    setMeshPreview(null);
    setMeshError(null);
    setMeshLoading(false);
    setMaterialLoading(false);
    setMaterialPreviewUrl(null);
    meshRequestRef.current += 1;
  }, [relativePath]);

  useEffect(() => {
    if (!result?.fallback && result?.listener_online !== false) return;
    let cancelled = false;
    const stop = onApiReady((api) => {
      void api.read_project_file(relativePath).then((res) => {
        if (!cancelled) setHexContent(res.content);
      });
    });
    return () => {
      cancelled = true;
      stop();
    };
  }, [relativePath, result?.fallback, result?.listener_online]);

  const copyAssetPath = useCallback(() => {
    const path = result?.asset_path;
    if (!path) return;
    void navigator.clipboard.writeText(path);
  }, [result?.asset_path]);

  const openInUefn = useCallback(() => {
    setOpenError(null);
    onApiReady((api) => {
      if (api.open_asset_in_uefn) {
        void api
          .open_asset_in_uefn(relativePath)
          .then((res) => {
            if (res && res.ok === false) {
              setOpenError(cleanPreviewError(res.error || "Could not open in UEFN."));
            }
          })
          .catch((err: unknown) => {
            setOpenError(cleanPreviewError(err instanceof Error ? err.message : "Could not open in UEFN."));
          });
        return;
      }
      void api.open_project_file(relativePath);
    });
  }, [relativePath]);

  const openVerseSource = useCallback(() => {
    const src = result?.verse_source;
    if (!src) return;
    verseEditor?.openFileAt(src, basename(src), { activate: true });
  }, [result?.verse_source, verseEditor]);

  const loadMeshPreview = useCallback(() => {
    setMeshError(null);
    setMeshLoading(true);
    const reqId = ++meshRequestRef.current;
    onApiReady((api) => {
      if (!api.load_static_mesh_preview) {
        if (reqId === meshRequestRef.current) {
          setMeshLoading(false);
          setMeshError("3D mesh preview is not available in this build.");
        }
        return;
      }
      void api
        .load_static_mesh_preview(relativePath)
        .then((res) => {
          if (reqId !== meshRequestRef.current) return;
          if (!meshPreviewMediaFromResult(res)) {
            setMeshPreview(null);
            setMeshError(cleanPreviewError(res?.error || "Could not export this asset as a StaticMesh."));
            return;
          }
          setMeshPreview(res);
          setMeshError(null);
        })
        .catch((err: unknown) => {
          if (reqId !== meshRequestRef.current) return;
          setMeshPreview(null);
          setMeshError(cleanPreviewError(err instanceof Error ? err.message : "Failed to load 3D preview"));
        })
        .finally(() => {
          if (reqId === meshRequestRef.current) setMeshLoading(false);
        });
    });
  }, [relativePath]);

  const loadMaterialPreview = useCallback(() => {
    setMeshError(null);
    setMaterialLoading(true);
    const reqId = ++meshRequestRef.current;
    onApiReady((api) => {
      if (!api.load_material_preview) {
        if (reqId === meshRequestRef.current) {
          setMaterialLoading(false);
          setMeshError("Material preview is not available in this build.");
        }
        return;
      }
      void api
        .load_material_preview(relativePath)
        .then((res) => {
          if (reqId !== meshRequestRef.current) return;
          if (!res?.ok || !res.preview_url) {
            setMaterialPreviewUrl(null);
            setMeshError(cleanPreviewError(res?.error || "Could not preview this material."));
            return;
          }
          setMaterialPreviewUrl(res.preview_url);
          setMeshError(null);
        })
        .catch((err: unknown) => {
          if (reqId !== meshRequestRef.current) return;
          setMaterialPreviewUrl(null);
          setMeshError(cleanPreviewError(err instanceof Error ? err.message : "Failed to load material preview"));
        })
        .finally(() => {
          if (reqId === meshRequestRef.current) setMaterialLoading(false);
        });
    });
  }, [relativePath]);

  const loadTexturePreview = useCallback(() => {
    setMeshError(null);
    setMaterialLoading(true);
    const reqId = ++meshRequestRef.current;
    onApiReady((api) => {
      if (!api.load_texture_preview) {
        if (reqId === meshRequestRef.current) {
          setMaterialLoading(false);
          setMeshError("Texture preview is not available in this build.");
        }
        return;
      }
      void api
        .load_texture_preview(relativePath)
        .then((res) => {
          if (reqId !== meshRequestRef.current) return;
          if (!res?.ok || !res.preview_url) {
            setMaterialPreviewUrl(null);
            setMeshError(cleanPreviewError(res?.error || "Could not preview this texture."));
            return;
          }
          setMaterialPreviewUrl(res.preview_url);
          setMeshError(null);
        })
        .catch((err: unknown) => {
          if (reqId !== meshRequestRef.current) return;
          setMaterialPreviewUrl(null);
          setMeshError(cleanPreviewError(err instanceof Error ? err.message : "Failed to load texture preview"));
        })
        .finally(() => {
          if (reqId === meshRequestRef.current) setMaterialLoading(false);
        });
    });
  }, [relativePath]);

  const pathKind = guessPreviewKind(relativePath, result?.asset_class);
  const previewKind = pathKind !== "other" ? pathKind : result?.preview_kind || pathKind;
  const canOfferMesh = canOfferStaticMeshPreview(
    relativePath,
    result?.supports_mesh_preview,
    result?.asset_class,
    previewKind,
  );
  const canOfferMaterial = canOfferMaterialPreview(
    relativePath,
    result?.supports_material_preview,
    result?.asset_class,
    previewKind,
  );
  const canOfferTexture = canOfferTexturePreview(
    relativePath,
    result?.supports_texture_preview,
    result?.asset_class,
    previewKind,
  );

  useEffect(() => {
    // Online: export/refresh from UEFN. Offline: still try mesh load so AppData cache can show.
    if (canOfferMesh) {
      if (meshPreview || meshLoading || meshError) return;
      loadMeshPreview();
      return;
    }
    if (!result?.listener_online) return;
    if (materialPreviewUrl || materialLoading) return;
    if (canOfferTexture) {
      loadTexturePreview();
      return;
    }
    if (canOfferMaterial) {
      loadMaterialPreview();
    }
  }, [
    result?.listener_online,
    canOfferMesh,
    canOfferMaterial,
    canOfferTexture,
    meshPreview,
    meshLoading,
    meshError,
    materialPreviewUrl,
    materialLoading,
    loadMeshPreview,
    loadMaterialPreview,
    loadTexturePreview,
  ]);

  const resolvedMeshMedia = useMemo(
    () => meshPreviewMediaFromResult(meshPreview),
    [
      meshPreview?.media_url,
      meshPreview?.media_base_url,
      meshPreview?.media_filename,
      meshPreview?.mime,
      meshPreview?.ok,
    ],
  );

  if (loading && !result) {
    return <div className="ui-status-muted">Loading asset preview…</div>;
  }

  if (error && !result) {
    return <div className="ui-status-error">{error}</div>;
  }

  if (!result) {
    return <div className="ui-status-muted">No preview available.</div>;
  }

  const sizeX = result.metadata?.size_x;
  const sizeY = result.metadata?.size_y;
  const sizeLabel =
    typeof sizeX === "number" && typeof sizeY === "number" ? `${sizeX} × ${sizeY}` : "";

  const isNiagara = previewKind === "niagara";

  const naniteNote =
    meshPreview?.metadata?.has_nanite === true
      ? "Nanite mesh — preview uses fallback geometry; Open in UEFN for full detail."
      : meshPreview?.metadata?.preview_note
        ? String(meshPreview.metadata.preview_note)
        : null;

  if (resolvedMeshMedia) {
    const meshOfflineBanner =
      result.listener_online === false
        ? `UEFN is offline — showing cached preview${meshPreview?.stale || result.stale ? " (may be outdated)" : ""}`
        : null;
    return (
      <div className="asset-preview-pane asset-preview-pane--mesh">
        {meshOfflineBanner ? <div className="asset-preview-offline asset-preview-mesh-banner">{meshOfflineBanner}</div> : null}
        {openError ? <div className="ui-status-error asset-preview-mesh-banner">{openError}</div> : null}
        {naniteNote ? <div className="asset-preview-meta asset-preview-mesh-banner">{naniteNote}</div> : null}
        <ModelFilePane
          relativePath={relativePath}
          resolvedMedia={resolvedMeshMedia}
          meshMetadata={meshPreview?.metadata || null}
          maxPixelRatio={1.5}
          toolbarExtras={
            <>
              <button type="button" className="settings-btn model-toolbar-btn" onClick={openInUefn}>
                Open in UEFN
              </button>
              {result.asset_path ? (
                <button type="button" className="settings-btn model-toolbar-btn" onClick={copyAssetPath}>
                  Copy asset path
                </button>
              ) : null}
            </>
          }
        />
      </div>
    );
  }

  if (canOfferMesh && result.listener_online && (meshLoading || !meshError)) {
    return (
      <div className="asset-preview-pane selectable-text">
        <div className="asset-preview-header">
          <div className="asset-preview-class">{result.asset_class || previewKind || "Unreal asset"}</div>
          <div className="asset-preview-path">{result.asset_path || relativePath}</div>
        </div>
        <div className="ui-status-muted">Loading 3D preview…</div>
        {openError ? <div className="ui-status-error">{openError}</div> : null}
      </div>
    );
  }

  if (canOfferMesh && !result.listener_online && meshLoading) {
    return (
      <div className="asset-preview-pane selectable-text">
        <div className="asset-preview-header">
          <div className="asset-preview-class">{result.asset_class || previewKind || "Unreal asset"}</div>
          <div className="asset-preview-path">{result.asset_path || relativePath}</div>
        </div>
        <div className="ui-status-muted">Loading cached 3D preview…</div>
      </div>
    );
  }

  const hasCachedImageMode = result.mode === "texture" || result.mode === "material" || result.mode === "image";
  const displayImage =
    materialPreviewUrl ||
    (hasCachedImageMode ? result.preview_url : null) ||
    (!canOfferTexture && !canOfferMaterial ? result.preview_url : null);
  const waitingForImage =
    !displayImage && result.listener_online && (canOfferTexture || canOfferMaterial) && (materialLoading || !meshError);
  const showingCachedPreview = Boolean(displayImage || resolvedMeshMedia) && !result.listener_online;
  const previewStale = Boolean(result.stale || meshPreview?.stale);
  const offlineBanner = showingCachedPreview
    ? `UEFN is offline — showing cached preview${previewStale ? " (may be outdated)" : ""}`
    : "UEFN is offline — start UEFN, then use “Open in UEFN”.";

  return (
    <div className="asset-preview-pane selectable-text">
      <div className="asset-preview-header">
        <div className="asset-preview-class">{result.asset_class || previewKind || "Unreal asset"}</div>
        <div className="asset-preview-path">{result.asset_path || relativePath}</div>
      </div>

      {!result.listener_online ? (
        <div className="asset-preview-offline">{offlineBanner}</div>
      ) : null}

      {displayImage ? (
        <div className="asset-preview-image-wrap">
          <img
            className="asset-preview-image"
            src={displayImage}
            alt={`Preview of ${basename(relativePath)}`}
            draggable={false}
          />
        </div>
      ) : waitingForImage ? (
        <div className="ui-status-muted">
          {canOfferTexture ? "Loading texture preview…" : "Loading material preview…"}
        </div>
      ) : null}

      {isNiagara ? (
        <div className="asset-preview-meta">
          Niagara / VFX can’t play inside Ducky’s Three.js viewer. Open in UEFN to scrub emitters.
        </div>
      ) : null}

      {result.mode === "verse" || result.verse_source ? (
        <div className="asset-preview-meta">
          Compiled Verse class
          {result.verse_source ? (
            <>
              {" "}
              — source: <code>{result.verse_source}</code>
            </>
          ) : null}
        </div>
      ) : null}

      <MetaRow label="Package" value={String(result.metadata?.package_name ?? "")} />
      <MetaRow label="Size" value={sizeLabel} />
      {typeof result.metadata?.material_slots === "number" ? (
        <MetaRow label="Material slots" value={String(result.metadata.material_slots)} />
      ) : null}

      <div className="asset-preview-actions">
        {meshError && result.listener_online && (canOfferMesh || canOfferMaterial || canOfferTexture) ? (
          <button
            type="button"
            className="settings-btn"
            onClick={() => {
              setMeshError(null);
              if (canOfferMesh) loadMeshPreview();
              else if (canOfferTexture) loadTexturePreview();
              else loadMaterialPreview();
            }}
            disabled={meshLoading || materialLoading}
          >
            Retry preview
          </button>
        ) : null}
        <button type="button" className="settings-btn" onClick={openInUefn}>
          Open in UEFN
        </button>
        {result.asset_path ? (
          <button type="button" className="settings-btn" onClick={copyAssetPath}>
            Copy asset path
          </button>
        ) : null}
        {result.verse_source && verseEditor ? (
          <button type="button" className="settings-btn" onClick={openVerseSource}>
            Open Verse source
          </button>
        ) : null}
      </div>

      {meshError && result.listener_online ? <div className="ui-status-error">{meshError}</div> : null}
      {meshError && !result.listener_online && !displayImage ? (
        <div className="ui-status-error">{meshError}</div>
      ) : null}
      {openError ? <div className="ui-status-error">{openError}</div> : null}

      {hexContent ? (
        <details
          className="asset-preview-hex"
          open={hexOpen}
          onToggle={(e) => setHexOpen((e.target as HTMLDetailsElement).open)}
        >
          <summary>Raw binary (hex)</summary>
          <CtrlWheelZoomRoot className="file-editor-pre" storageKey={`uefn-panel-hex:${relativePath}`}>
            {hexContent}
          </CtrlWheelZoomRoot>
        </details>
      ) : null}
    </div>
  );
}
