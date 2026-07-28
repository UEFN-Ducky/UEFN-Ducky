import { useCallback, useEffect, useState } from "react";

import { onApiReady } from "../hooks/onApiReady";
import { useWatchProjectFile } from "../hooks/useWatchProjectFile";
import { basename } from "../verse-editor/utils/isVerseFile";
import { CtrlWheelZoomRoot } from "./CtrlWheelZoomRoot";

import "../asset-preview/asset-preview.css";

interface ImageFilePaneProps {
  relativePath: string;
}

export function ImageFilePane({ relativePath }: ImageFilePaneProps) {
  const [mediaUrl, setMediaUrl] = useState<string | null>(null);
  const [mime, setMime] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [imgFailed, setImgFailed] = useState(false);

  const load = useCallback(
    (options?: { showLoading?: boolean }) => {
      const showLoading = options?.showLoading ?? true;
      return onApiReady((api) => {
        if (showLoading) setLoading(true);
        setError(null);
        setImgFailed(false);
        const fetchUrl =
          api.project_file_media_url?.(relativePath) ??
          api.read_project_file(relativePath).then((r) => ({
            path: r.path,
            media_url: r.media_url || "",
            mime: r.mime || "",
            kind: r.kind || "",
          }));
        void Promise.resolve(fetchUrl)
          .then((result) => {
            const url = result.media_url || "";
            if (!url) {
              setMediaUrl(null);
              setError("This image can’t be previewed in the panel.");
              return;
            }
            // Bust cache when the file changes on disk.
            const sep = url.includes("?") ? "&" : "?";
            setMediaUrl(`${url}${sep}t=${Date.now()}`);
            setMime(result.mime || "");
          })
          .catch((e: unknown) => {
            setMediaUrl(null);
            setError(e instanceof Error ? e.message : "Failed to load image");
          })
          .finally(() => {
            if (showLoading) setLoading(false);
          });
      });
    },
    [relativePath],
  );

  useEffect(() => {
    const stop = load();
    return () => {
      stop();
    };
  }, [load]);

  useWatchProjectFile(
    relativePath,
    () => {
      void load({ showLoading: false });
    },
    { enabled: !loading },
  );

  if (loading) {
    return <div className="ui-status-muted">Loading {basename(relativePath)}…</div>;
  }

  if (error || !mediaUrl || imgFailed) {
    return (
      <div className="file-editor-pane file-editor-pane-layout binary-file-pane">
        <div className="ui-status-muted">
          {error || "This image can’t be displayed in the panel."}
        </div>
        <div className="asset-preview-meta">{basename(relativePath)}</div>
      </div>
    );
  }

  return (
    <div className="file-editor-pane file-editor-pane-layout asset-preview-pane">
      <div className="asset-preview-header">
        <div className="asset-preview-class">{basename(relativePath)}</div>
        <div className="asset-preview-path">{relativePath}</div>
        {mime ? <div className="asset-preview-meta">{mime}</div> : null}
      </div>
      <CtrlWheelZoomRoot
        className="asset-preview-image-wrap"
        storageKey={`uefn-panel-image-zoom:${relativePath}`}
      >
        <img
          className="asset-preview-image"
          src={mediaUrl}
          alt={basename(relativePath)}
          draggable={false}
          onError={() => setImgFailed(true)}
        />
      </CtrlWheelZoomRoot>
    </div>
  );
}
