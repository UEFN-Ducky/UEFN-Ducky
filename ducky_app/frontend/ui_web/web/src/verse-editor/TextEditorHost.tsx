import { useCallback, useEffect, useRef, useState } from "react";

import type { editor } from "monaco-editor";

import { useVerseEditor } from "./VerseEditorProvider";
import { MONACO_EMBEDDED_OVERFLOW_OPTIONS } from "./monaco/embeddedEditorOverflow";
import { setupMonaco } from "./monaco/setupMonaco";
import { applyVerseMonacoTheme } from "./monaco/verseTheme";
import {
  applyMonacoEditorFont,
  ensureMonacoFontReady,
  monacoFontSizeForZoom,
} from "./monaco/resolveMonacoFontFamily";
import { useMonacoEditorLayout } from "./monaco/useMonacoEditorLayout";
import { forceFullTokenization } from "./monaco/forceFullTokenization";
import { useAppearance } from "../theme/AppearanceContext";
import { readVerseFile, writeVerseFile, recordExternalFileChange } from "./api/verseEditorApi";
import { notifyMissingProjectFile } from "./diagnostics/purgeDeletedFile";
import { toEditorAbsolutePath } from "./lsp/uriUtils";
import { normPath, isPanelReadOnlyFile, monacoLanguageForPath } from "./utils/isVerseFile";
import { useWatchProjectFile } from "../hooks/useWatchProjectFile";
import { formatZoomPercent, useCtrlWheelZoom } from "../hooks/useCtrlWheelZoom";
import { useMinimapEnabled } from "../hooks/useMinimapEnabled";
import {
  clearQuickOpenEditor,
  installQuickOpenKeybindings,
  setQuickOpenEditor,
} from "../components/quick-open/quickOpenEditorBridge";
import { ReplayOverlay } from "./replay/ReplayOverlay";

import "./verse-editor.css";

interface TextEditorHostProps {
  relativePath: string;
  projectRoot?: string;
  readOnly?: boolean;
}

export function TextEditorHost({ relativePath, projectRoot = "", readOnly = false }: TextEditorHostProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<typeof import("monaco-editor") | null>(null);
  const savedContentRef = useRef("");
  const {
    setDirty,
    dirtyPaths,
    registerSaveHandler,
    unregisterSaveHandler,
    registerEditor,
    unregisterEditor,
    replayController,
  } = useVerseEditor();
  const { cssVars, foundation, appearanceReady } = useAppearance();
  const appearanceRef = useRef({ cssVars, foundation });
  appearanceRef.current = { cssVars, foundation };

  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const path = normPath(relativePath);
  const locked = readOnly || isPanelReadOnlyFile(relativePath);
  const language = monacoLanguageForPath(relativePath);

  const { ref: zoomHostRef, zoom, indicatorVisible } = useCtrlWheelZoom({
    storageKey: `uefn-panel-file-zoom:${path}`,
    capture: true,
  });
  const { enabled: minimapEnabled } = useMinimapEnabled(path);
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;

  const dirty = dirtyPaths.has(path);
  const handleSaveRef = useRef<() => Promise<boolean>>(async () => false);

  useEffect(() => {
    if (!projectRoot || !appearanceReady) return;

    let cancelled = false;
    setReady(false);
    setError(null);

    void (async () => {
      for (let i = 0; i < 60 && !containerRef.current; i += 1) {
        await new Promise((r) => requestAnimationFrame(r));
      }
      const container = containerRef.current;
      if (!container || cancelled) return;

      try {
        const { content, path: canonicalPath } = await readVerseFile(relativePath);
        if (cancelled) return;
        savedContentRef.current = content;

        const monacoApi = await setupMonaco();
        if (cancelled) return;
        monacoRef.current = monacoApi;

        applyVerseMonacoTheme(
          monacoApi,
          appearanceRef.current.cssVars,
          appearanceRef.current.foundation,
        );

        const fontFamily = await ensureMonacoFontReady(
          appearanceRef.current.cssVars,
          monacoFontSizeForZoom(zoomRef.current),
        );
        if (cancelled) return;

        const absPath = toEditorAbsolutePath(projectRoot, canonicalPath);
        const uri = monacoApi.Uri.file(absPath);
        const existingModel = monacoApi.editor.getModel(uri);
        if (existingModel) {
          try {
            existingModel.dispose();
          } catch {
            /* already torn down */
          }
        }

        const model = monacoApi.editor.createModel(content, language, uri);
        if (editorRef.current) {
          try {
            editorRef.current.dispose();
          } catch {
            /* layout race */
          }
        }

        const ed = monacoApi.editor.create(container, {
          model,
          theme: "verse-dark",
          automaticLayout: false,
          fontSize: monacoFontSizeForZoom(zoomRef.current),
          mouseWheelZoom: false,
          fontFamily,
          fontLigatures: false,
          tabSize: language === "python" ? 4 : 2,
          insertSpaces: true,
          lineNumbers: "on",
          minimap: { enabled: minimapEnabled },
          wordWrap: "off",
          scrollBeyondLastLine: false,
          readOnly: locked,
          smoothScrolling: true,

          ...MONACO_EMBEDDED_OVERFLOW_OPTIONS,
        });

        editorRef.current = ed;

        // Register in the shared editor registry so replay/follow-code can find this
        // buffer (VerseEditorHost does the same). Without it, replaying a non-verse
        // edit opens the file but the walkthrough can never locate the editor.
        registerEditor(path, ed);

        ed.onDidFocusEditorWidget(() => {
          setQuickOpenEditor(ed, path);
        });

        ed.onDidChangeModelContent(() => {
          if (locked) return;
          setDirty(path, ed.getValue() !== savedContentRef.current);
        });

        ed.addCommand(monacoApi.KeyMod.CtrlCmd | monacoApi.KeyCode.KeyS, () => {
          void handleSaveRef.current();
        });

        installQuickOpenKeybindings(ed, monacoApi);

        if (!cancelled) {
          setReady(true);
          // Colour the whole file on open. With automaticLayout off the editor mounts with a
          // zero-height viewport and tokenizes nothing until a scroll/edit; force it now
          // (rebuilds the tokenizer via a language round-trip, tokenizes every line, repaints).
          forceFullTokenization(monacoApi, ed);
        }
      } catch (e) {
        if (!cancelled) {
          const raw = e instanceof Error ? e.message : "Failed to load editor";
          if (raw.includes("Not a file")) {
            notifyMissingProjectFile(path);
            return;
          }
          setError(raw);
        }
      }
    })();

    return () => {
      cancelled = true;
      unregisterEditor(path);
      const ed = editorRef.current;
      editorRef.current = null;
      if (ed) {
        clearQuickOpenEditor(ed, path);
        const model = ed.getModel();
        try {
          ed.dispose();
        } catch {
          /* unmount race */
        }
        if (model && !model.isDisposed()) {
          try {
            model.dispose();
          } catch {
            /* already disposed */
          }
        }
      }
    };
  }, [
    relativePath,
    projectRoot,
    appearanceReady,
    language,
    locked,
    minimapEnabled,
    path,
    setDirty,
    registerEditor,
    unregisterEditor,
  ]);

  useEffect(() => {
    if (!monacoRef.current || !appearanceReady) return;
    applyVerseMonacoTheme(monacoRef.current, cssVars, foundation, [editorRef.current]);
    void ensureMonacoFontReady(cssVars).then((fontFamily) => {
      const ed = editorRef.current;
      if (!ed) return;
      applyMonacoEditorFont(ed, fontFamily, monacoFontSizeForZoom(zoom));
      try {
        if (!ed.getModel()?.isDisposed()) ed.layout();
      } catch {
        /* disposed */
      }
    });
  }, [cssVars, foundation, appearanceReady, zoom]);

  const handleSave = useCallback(async (): Promise<boolean> => {
    if (locked) return true;
    const ed = editorRef.current;
    if (!ed || saving) return !dirty;
    if (ed.getValue() === savedContentRef.current) {
      setDirty(path, false);
      return true;
    }
    setSaving(true);
    try {
      const content = ed.getValue();
      await writeVerseFile(path, content);
      savedContentRef.current = content;
      setDirty(path, false);
      return true;
    } catch {
      return false;
    } finally {
      setSaving(false);
    }
  }, [path, saving, setDirty, dirty, locked]);

  handleSaveRef.current = handleSave;

  const applyDiskContent = useCallback(
    (content: string) => {
      const ed = editorRef.current;
      if (!ed) return;
      const model = ed.getModel();
      if (!model || model.isDisposed()) return;
      savedContentRef.current = content;
      if (ed.getValue() !== content) model.setValue(content);
      setDirty(path, false);
    },
    [path, setDirty],
  );

  const handleExternalFileChange = useCallback(async () => {
    try {
      const { content } = await readVerseFile(path);
      const ed = editorRef.current;
      if (!ed) return;
      const current = ed.getValue();
      if (content === current) {
        savedContentRef.current = content;
        setDirty(path, false);
        return;
      }
      const previousContent =
        current !== savedContentRef.current ? current : savedContentRef.current;
      await recordExternalFileChange(path, previousContent, content);
      applyDiskContent(content);
    } catch (e) {
      if (e instanceof Error && e.message.includes("Not a file")) {
        notifyMissingProjectFile(path);
      }
    }
  }, [path, applyDiskContent, setDirty]);

  useWatchProjectFile(path, () => void handleExternalFileChange(), {
    enabled: ready && !error,
  });

  useEffect(() => {
    registerSaveHandler(path, handleSave);
    return () => unregisterSaveHandler(path);
  }, [path, handleSave, registerSaveHandler, unregisterSaveHandler]);

  const isResizing = useMonacoEditorLayout(containerRef, editorRef, ready && !error);

  return (
    <div className={`verse-editor-host${isResizing ? " verse-editor-host--resizing" : ""}`}>
      {error ? (
        <div className="ui-status-error">{error}</div>
      ) : !ready ? (
        <div className="ui-status-muted">Loading editor…</div>
      ) : null}
      <div ref={zoomHostRef} className="verse-editor-body">
        <ReplayOverlay path={path} controller={replayController} />
        <div
          ref={containerRef}
          className={`verse-editor-container${ready && !error ? " is-visible" : ""}`}
        />
        <div
          className={`ctrl-wheel-zoom-indicator${indicatorVisible ? " ctrl-wheel-zoom-indicator--visible" : ""}`}
          aria-live="polite"
          aria-hidden={!indicatorVisible}
        >
          {formatZoomPercent(zoom)}
        </div>
      </div>
      {saving ? <div className="verse-editor-saving-indicator">Saving…</div> : null}
    </div>
  );
}
