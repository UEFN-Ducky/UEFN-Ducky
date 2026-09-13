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

  const {
    fileSessions,
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
    setSaving(false);
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
        const session = fileSessions.acquire(path, content, () => {
          // Goto-definition/peek targets are created outside the session cache and Monaco
          // refuses a second model at the same URI — replace such a stray model.
          monacoApi.editor.getModel(uri)?.dispose();
          return monacoApi.editor.createModel(content, language, uri);
        });
        const model = session.model;
        if (model.getValue() !== content && session.savedContent !== content) {
          // Disk changed while this tab was hidden and the buffer holds unsaved edits.
          // The mtime watcher only starts at mount, so apply its policy here: disk
          // wins, the edited buffer is recorded to history and stays one undo away.
          await recordExternalFileChange(path, model.getValue(), content);
          if (cancelled) return;
          fileSessions.applyDiskContent(path, content);
        }
        setDirty(path, fileSessions.isDirty(path));

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
        if (session.viewState) ed.restoreViewState(session.viewState);

        // Register in the shared editor registry so replay/follow-code can find this
        // buffer (VerseEditorHost does the same). Without it, replaying a non-verse
        // edit opens the file but the walkthrough can never locate the editor.
        registerEditor(path, ed);

        ed.onDidFocusEditorWidget(() => {
          setQuickOpenEditor(ed, path);
        });

        ed.onDidChangeModelContent(() => {
          if (locked) return;
          setDirty(path, ed.getValue() !== fileSessions.savedContent(path));
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
        const session = fileSessions.get(path);
        if (session && session.model === ed.getModel()) session.viewState = ed.saveViewState();
        try {
          ed.dispose();
        } catch {
          /* unmount race */
        }
      }
    };
  }, [
    fileSessions,
    relativePath,
    projectRoot,
    appearanceReady,
    language,
    locked,
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

  useEffect(() => { editorRef.current?.updateOptions({ minimap: { enabled: minimapEnabled } }); }, [minimapEnabled, ready]);

  const handleSave = useCallback(async (): Promise<boolean> => {
    if (locked) return true;
    const ed = editorRef.current;
    if (!ed) return !dirty;
    if (ed.getValue() === fileSessions.savedContent(path)) {
      setDirty(path, false);
      return true;
    }
    setSaving(true);
    try {
      const saved = await fileSessions.save(path, writeVerseFile);
      setDirty(path, fileSessions.isDirty(path));
      return saved;
    } catch {
      return false;
    } finally {
      if (editorRef.current === ed) setSaving(false);
    }
  }, [path, fileSessions, setDirty, dirty, locked]);

  handleSaveRef.current = handleSave;

  const applyDiskContent = useCallback(
    (content: string) => {
      const ed = editorRef.current;
      if (!ed) return;
      const model = ed.getModel();
      if (!model || model.isDisposed()) return;
      fileSessions.markSaved(path, content);
      if (ed.getValue() !== content) model.setValue(content);
      setDirty(path, false);
    },
    [path, fileSessions, setDirty],
  );

  const handleExternalFileChange = useCallback(async () => {
    const ed = editorRef.current;
    const model = ed?.getModel();
    if (!ed || !model || model.isDisposed()) return;
    const version = model.getVersionId();
    const isCurrent = () => editorRef.current === ed && !model.isDisposed() && model.getVersionId() === version;
    try {
      const { content } = await readVerseFile(path);
      if (!isCurrent()) return;
      const current = ed.getValue();
      if (content === current) {
        fileSessions.markSaved(path, content);
        setDirty(path, false);
        return;
      }
      // Ignore the disk echo of our own save while the user keeps typing.
      if (content === fileSessions.savedContent(path)) return;
      const previousContent =
        current !== fileSessions.savedContent(path) ? current : fileSessions.savedContent(path);
      await recordExternalFileChange(path, previousContent, content);
      if (!isCurrent()) return;
      applyDiskContent(content);
    } catch (e) {
      if (isCurrent() && e instanceof Error && e.message.includes("Not a file")) {
        notifyMissingProjectFile(path);
      }
    }
  }, [path, fileSessions, applyDiskContent, setDirty]);

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
