import { lazy, Suspense, useCallback, useEffect, useState } from "react";

import { isPreviewableBinary } from "../asset-preview";
import { AssetPreviewPane } from "../asset-preview/AssetPreviewPane";
import { onApiReady } from "../hooks/onApiReady";
import { useWatchProjectFile } from "../hooks/useWatchProjectFile";

import {
  isVerseFile,
  isEditableTextFile,
  isPanelReadOnlyFile,
  isExtEncodedPath,
  isBinaryProjectFile,
} from "../verse-editor";
import {
  classifyFilePath,
  isAudioFilePath,
  isImageFilePath,
  isModelFilePath,
  isVideoFilePath,
} from "../verse-editor/utils/fileKind";

import { AudioFilePane } from "./AudioFilePane";
import { CtrlWheelZoomRoot } from "./CtrlWheelZoomRoot";
import { ImageFilePane } from "./ImageFilePane";
import { ModelFilePane } from "./ModelFilePane";
import { VideoFilePane } from "./VideoFilePane";

const TextEditorHost = lazy(() =>
  import("../verse-editor/TextEditorHost").then((m) => ({ default: m.TextEditorHost })),
);

const VerseEditorHost = lazy(() =>
  import("../verse-editor/VerseEditorHost").then((m) => ({ default: m.VerseEditorHost })),
);

interface FileEditorPaneProps {
  relativePath: string;
  variant?: "default" | "focus";
}

function ReadOnlyFilePane({ relativePath }: FileEditorPaneProps) {
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const loadContent = useCallback(
    (options?: { showLoading?: boolean }) => {
      const showLoading = options?.showLoading ?? true;
      return onApiReady((api) => {
        if (showLoading) setLoading(true);
        setError(null);
        void api
          .read_project_file(relativePath)
          .then((result) => {
            if (result.kind === "image" || result.media_url) {
              setError("This file is an image — reopen the tab to preview it.");
              setContent(null);
              return;
            }
            if (result.binary_preview === "true") {
              setContent(result.content);
              return;
            }
            setContent(result.content);
          })
          .catch((e: unknown) => {
            setContent(null);
            setError(e instanceof Error ? e.message : "Failed to read file");
          })
          .finally(() => {
            if (showLoading) setLoading(false);
          });
      });
    },
    [relativePath],
  );

  useEffect(() => {
    const stop = loadContent();
    return () => {
      stop();
    };
  }, [loadContent]);

  useWatchProjectFile(
    relativePath,
    () => {
      void loadContent({ showLoading: false });
    },
    { enabled: !loading },
  );

  if (loading) {
    return <div className="ui-status-muted">Loading {relativePath}…</div>;
  }

  if (error) {
    return <div className="ui-status-error">{error}</div>;
  }

  return (
    <div className="selectable-text file-editor-pane file-editor-pane-layout">
      <div data-no-translate className="file-editor-source">
        <CtrlWheelZoomRoot
          className="file-editor-pre"
          storageKey={`uefn-panel-file-zoom:${relativePath}`}
        >
          {content}
        </CtrlWheelZoomRoot>
      </div>
    </div>
  );
}

/** Binary / Unreal asset files. Opt-in "View raw" shows the hex head, VS Code style. */
function BinaryFilePane({ relativePath }: FileEditorPaneProps) {
  const [showRaw, setShowRaw] = useState(false);

  if (showRaw) {
    return <ReadOnlyFilePane relativePath={relativePath} />;
  }

  return (
    <div className="file-editor-pane file-editor-pane-layout binary-file-pane">
      <div className="ui-status-muted">This file can’t be opened in the editor.</div>
      <button type="button" className="settings-btn" onClick={() => setShowRaw(true)}>
        View raw
      </button>
    </div>
  );
}

/** Loads once to catch backend binary_preview for extensionless / mislabeled files. */
function GuardedTextEditor({
  relativePath,
  projectRoot,
  readOnly,
}: {
  relativePath: string;
  projectRoot: string;
  readOnly: boolean;
}) {
  const [gate, setGate] = useState<
    "loading" | "text" | "binary" | "image" | "model" | "audio" | "video"
  >("loading");

  useEffect(() => {
    let cancelled = false;
    const stop = onApiReady((api) => {
      void api
        .read_project_file(relativePath)
        .then((result) => {
          if (cancelled) return;
          if (result.kind === "model") {
            setGate("model");
          } else if (result.kind === "image" && result.media_url) {
            setGate("image");
          } else if (result.kind === "audio" && result.media_url) {
            setGate("audio");
          } else if (result.kind === "video" && result.media_url) {
            setGate("video");
          } else if (result.binary_preview === "true" || result.kind === "binary" || result.kind === "unreal_asset") {
            setGate("binary");
          } else {
            setGate("text");
          }
        })
        .catch(() => {
          if (!cancelled) setGate("text");
        });
    });
    return () => {
      cancelled = true;
      stop();
    };
  }, [relativePath]);

  if (gate === "loading") {
    return <div className="ui-status-muted">Loading…</div>;
  }
  if (gate === "model") {
    return <ModelFilePane relativePath={relativePath} />;
  }
  if (gate === "image") {
    return <ImageFilePane relativePath={relativePath} />;
  }
  if (gate === "audio") {
    return <AudioFilePane relativePath={relativePath} />;
  }
  if (gate === "video") {
    return <VideoFilePane relativePath={relativePath} />;
  }
  if (gate === "binary") {
    return <BinaryFilePane relativePath={relativePath} />;
  }
  return (
    <Suspense fallback={<div className="ui-status-muted">Loading editor…</div>}>
      <div className="file-editor-pane file-editor-pane-layout">
        {/* Source only — toolbar/chrome outside monaco may translate visually. */}
        <div data-no-translate className="file-editor-source">
          <TextEditorHost relativePath={relativePath} projectRoot={projectRoot} readOnly={readOnly} />
        </div>
      </div>
    </Suspense>
  );
}

export function FileEditorPane({ relativePath }: FileEditorPaneProps) {
  const [verseEnabled, setVerseEnabled] = useState<boolean | null>(null);
  const [projectRoot, setProjectRoot] = useState("");

  useEffect(() => {
    return onApiReady((api) => {
      void Promise.all([api.is_verse_editor_enabled(), api.get_project_info()])
        .then(([enabled, info]) => {
          setVerseEnabled(enabled);
          setProjectRoot(info.path || "");
        })
        .catch(() => {
          setVerseEnabled(false);
        });
    });
  }, []);

  const kind = classifyFilePath(relativePath);

  // Images / 3D models / audio / video first — never open as hex/Monaco.
  if (kind === "image" || isImageFilePath(relativePath)) {
    return <ImageFilePane relativePath={relativePath} />;
  }
  if (kind === "model" || isModelFilePath(relativePath)) {
    return <ModelFilePane relativePath={relativePath} />;
  }
  if (kind === "audio" || isAudioFilePath(relativePath)) {
    return <AudioFilePane relativePath={relativePath} />;
  }
  if (kind === "video" || isVideoFilePath(relativePath)) {
    return <VideoFilePane relativePath={relativePath} />;
  }

  // Files dragged in from Explorer (ext:).
  if (isExtEncodedPath(relativePath)) {
    if (kind === "binary" || kind === "unreal_asset" || isBinaryProjectFile(relativePath)) {
      return <BinaryFilePane relativePath={relativePath} />;
    }
    if (!projectRoot) {
      return <div className="ui-status-muted">Loading…</div>;
    }
    return (
      <GuardedTextEditor relativePath={relativePath} projectRoot={projectRoot} readOnly={false} />
    );
  }

  if (verseEnabled === null) {
    return <div className="ui-status-muted">Loading…</div>;
  }

  const readOnly = isPanelReadOnlyFile(relativePath);

  if (verseEnabled && isVerseFile(relativePath)) {
    if (!projectRoot) {
      return <div className="ui-status-muted">Loading project…</div>;
    }
    return (
      <Suspense fallback={<div className="ui-status-muted">Loading Verse editor…</div>}>
        <div className="file-editor-pane file-editor-pane-layout">
          <VerseEditorHost relativePath={relativePath} projectRoot={projectRoot} readOnly={readOnly} />
        </div>
      </Suspense>
    );
  }

  // Text (including .gitignore / Makefile / extensionless candidates) → Monaco.
  if (verseEnabled && (isEditableTextFile(relativePath) || kind === "text")) {
    if (!projectRoot) {
      return <div className="ui-status-muted">Loading project…</div>;
    }
    return (
      <GuardedTextEditor relativePath={relativePath} projectRoot={projectRoot} readOnly={readOnly} />
    );
  }

  if (isPreviewableBinary(relativePath) || kind === "unreal_asset") {
    return <AssetPreviewPane relativePath={relativePath} />;
  }

  if (kind === "binary") {
    return <BinaryFilePane relativePath={relativePath} />;
  }

  return <ReadOnlyFilePane relativePath={relativePath} />;
}
