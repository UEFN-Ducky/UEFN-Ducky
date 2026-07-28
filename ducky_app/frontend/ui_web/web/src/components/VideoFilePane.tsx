import { useCallback, useEffect, useRef, useState } from "react";

import { onApiReady } from "../hooks/onApiReady";
import { useWatchProjectFile } from "../hooks/useWatchProjectFile";
import { basename } from "../verse-editor/utils/isVerseFile";

import "../asset-preview/asset-preview.css";

interface VideoFilePaneProps {
  relativePath: string;
}

export function VideoFilePane({ relativePath }: VideoFilePaneProps) {
  const [mediaUrl, setMediaUrl] = useState<string | null>(null);
  const [mime, setMime] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [playbackFailed, setPlaybackFailed] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  const load = useCallback(
    (options?: { showLoading?: boolean }) => {
      const showLoading = options?.showLoading ?? true;
      return onApiReady((api) => {
        if (showLoading) setLoading(true);
        setError(null);
        setPlaybackFailed(false);
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
              setError("This video can’t be previewed in the panel.");
              return;
            }
            const sep = url.includes("?") ? "&" : "?";
            setMediaUrl(`${url}${sep}t=${Date.now()}`);
            setMime(result.mime || "");
          })
          .catch((e: unknown) => {
            setMediaUrl(null);
            setError(e instanceof Error ? e.message : "Failed to load video");
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

  useEffect(() => {
    videoRef.current?.load();
  }, [mediaUrl]);

  if (loading) {
    return <div className="ui-status-muted">Loading {basename(relativePath)}…</div>;
  }

  if (error || !mediaUrl) {
    return (
      <div className="file-editor-pane file-editor-pane-layout binary-file-pane">
        <div className="ui-status-muted">
          {error || "This video can’t be played in the panel."}
        </div>
        <div className="asset-preview-meta">{basename(relativePath)}</div>
      </div>
    );
  }

  return (
    <div className="file-editor-pane file-editor-pane-layout asset-preview-pane media-file-pane">
      <div className="asset-preview-header">
        <div className="asset-preview-class">{basename(relativePath)}</div>
        <div className="asset-preview-path">{relativePath}</div>
        {mime ? <div className="asset-preview-meta">{mime}</div> : null}
      </div>
      <div className="media-file-player-wrap media-file-player-wrap--video">
        <video
          ref={videoRef}
          className="media-file-video"
          controls
          playsInline
          preload="metadata"
          src={mediaUrl}
          onError={() => setPlaybackFailed(true)}
        />
        {playbackFailed ? (
          <div className="ui-status-error media-file-error">
            This browser can’t decode this video’s codec.
          </div>
        ) : null}
      </div>
    </div>
  );
}
