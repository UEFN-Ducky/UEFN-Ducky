import { useCallback, useEffect, useRef, useState } from "react";

import { onApiReady } from "../hooks/onApiReady";
import { useWatchProjectFile } from "../hooks/useWatchProjectFile";
import { basename } from "../verse-editor/utils/isVerseFile";

import "../theme/styles/media-file.css";

interface AudioFilePaneProps {
  relativePath: string;
}

export function AudioFilePane({ relativePath }: AudioFilePaneProps) {
  const [mediaUrl, setMediaUrl] = useState<string | null>(null);
  const [mime, setMime] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [playbackFailed, setPlaybackFailed] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);

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
              setError("This audio file can’t be previewed in the panel.");
              return;
            }
            const sep = url.includes("?") ? "&" : "?";
            setMediaUrl(`${url}${sep}t=${Date.now()}`);
            setMime(result.mime || "");
          })
          .catch((e: unknown) => {
            setMediaUrl(null);
            setError(e instanceof Error ? e.message : "Failed to load audio");
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
    audioRef.current?.load();
  }, [mediaUrl]);

  if (loading) {
    return <div className="ui-status-muted">Loading {basename(relativePath)}…</div>;
  }

  if (error || !mediaUrl) {
    return (
      <div className="file-editor-pane file-editor-pane-layout binary-file-pane">
        <div className="ui-status-muted">
          {error || "This audio file can’t be played in the panel."}
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
      <div className="media-file-player-wrap media-file-player-wrap--audio">
        <audio
          ref={audioRef}
          className="media-file-audio"
          controls
          preload="metadata"
          src={mediaUrl}
          onError={() => setPlaybackFailed(true)}
        />
        {playbackFailed ? (
          <div className="ui-status-error media-file-error">
            This browser can’t decode this audio file’s codec.
          </div>
        ) : null}
      </div>
    </div>
  );
}
