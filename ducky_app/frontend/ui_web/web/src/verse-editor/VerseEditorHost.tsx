import { useCallback, useEffect, useRef, useState } from "react";

import type { editor } from "monaco-editor";

import { useVerseEditor } from "./VerseEditorProvider";

import { setupMonaco } from "./monaco/setupMonaco";

import { applyVerseMonacoTheme } from "./monaco/verseTheme";
import {
  applyMonacoEditorFont,
  ensureMonacoFontReady,
  monacoFontSizeForZoom,
} from "./monaco/resolveMonacoFontFamily";
import { MONACO_EMBEDDED_OVERFLOW_OPTIONS } from "./monaco/embeddedEditorOverflow";
import { useMonacoEditorLayout } from "./monaco/useMonacoEditorLayout";
import { forceFullTokenization } from "./monaco/forceFullTokenization";

import { useAppearance } from "../theme/AppearanceContext";

import { readVerseFile, writeVerseFile, recordExternalFileChange } from "./api/verseEditorApi";

import { refreshOpenFileDiagnosticsNow } from "./diagnostics/refreshOpenFileDiagnostics";
import { notifyMissingProjectFile } from "./diagnostics/purgeDeletedFile";
import { isVerseDiagnosticsAutoCheckEnabled } from "./diagnostics/verseDiagnosticsSettings";

import { useVerseLsp } from "./lsp/useVerseLsp";

import { toEditorAbsolutePath } from "./lsp/uriUtils";
import { verseLspLog, verseLspLogError } from "./lsp/verseLspDebug";
import { verseEditorLogError } from "./verseEditorLog";

import { normPath, isPanelReadOnlyFile } from "./utils/isVerseFile";
import { setFocusedAskAiEditor } from "./askAi/askAiEditorRef";
import {
  clearQuickOpenEditor,
  installQuickOpenKeybindings,
  setQuickOpenEditor,
} from "../components/quick-open/quickOpenEditorBridge";
import { useWatchProjectFile } from "../hooks/useWatchProjectFile";
import { formatZoomPercent, useCtrlWheelZoom } from "../hooks/useCtrlWheelZoom";
import { useMinimapEnabled } from "../hooks/useMinimapEnabled";

import { ReplayOverlay } from "./replay/ReplayOverlay";

import "./verse-editor.css";



interface VerseEditorHostProps {
  relativePath: string;
  projectRoot?: string;
  readOnly?: boolean;
}

export function VerseEditorHost({ relativePath, projectRoot = "", readOnly = false }: VerseEditorHostProps) {

  const containerRef = useRef<HTMLDivElement>(null);

  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);

  const monacoRef = useRef<typeof import("monaco-editor") | null>(null);

  const savedContentRef = useRef("");

  const {

    registerEditor,

    unregisterEditor,

    setDirty,

    dirtyPaths,

    playbackLocked,

    registerSaveHandler,

    unregisterSaveHandler,

    historyPreview,

    clearHistoryPreview,

    registerEditorBridge,

    unregisterEditorBridge,

    bumpHistoryRefresh,

    replayController,

  } = useVerseEditor();

  const { cssVars, foundation, appearanceReady } = useAppearance();

  const appearanceRef = useRef({ cssVars, foundation });
  appearanceRef.current = { cssVars, foundation };



  const [ready, setReady] = useState(false);

  const [error, setError] = useState<string | null>(null);

  const [saving, setSaving] = useState(false);

  const [saveError, setSaveError] = useState<string | null>(null);

  const [monacoInstance, setMonacoInstance] = useState<typeof import("monaco-editor") | null>(null);

  const [editorInstance, setEditorInstance] = useState<editor.IStandaloneCodeEditor | null>(null);



  const path = normPath(relativePath);
  const locked = readOnly || isPanelReadOnlyFile(relativePath);

  const { ref: zoomHostRef, zoom, indicatorVisible } = useCtrlWheelZoom({
    storageKey: `uefn-panel-file-zoom:${path}`,
    capture: true,
  });
  const { enabled: minimapEnabled } = useMinimapEnabled(path);
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;

  const dirty = dirtyPaths.has(path);

  const handleSaveRef = useRef<() => Promise<boolean>>(async () => false);



  const { lspReady } = useVerseLsp(monacoInstance, editorInstance, path, projectRoot, playbackLocked);

  // The onDidChangeModelContent handler is wired once inside the init effect (which only
  // re-runs on path/projectRoot/appearance). Route `playbackLocked` through a ref so the
  // handler never reads a stale mount-time value when it toggles during agent playback.
  const playbackLockedRef = useRef(playbackLocked);
  playbackLockedRef.current = playbackLocked;



  useEffect(() => {

    if (!projectRoot) {
      verseLspLog("editor", "init waiting for projectRoot", { path });
      return;
    }

    if (!appearanceReady) {
      verseLspLog("editor", "init waiting for appearance", { path });
      return;
    }

    verseLspLog("editor", "init start", { path, projectRoot });

    let cancelled = false;

    setReady(false);

    setError(null);



    void (async () => {

      for (let i = 0; i < 60 && !containerRef.current; i += 1) {

        await new Promise((r) => requestAnimationFrame(r));

      }

      const container = containerRef.current;

      if (!container || cancelled) {

        if (!cancelled && !container) {
          verseLspLogError("editor", "container ref missing after wait", { path });
          setError("Editor failed to mount — try closing and reopening the tab");

        }

        return;

      }

      verseLspLog("editor", "container ready");



      try {

        verseLspLog("editor", "readVerseFile", { path });
        const { content, path: canonicalPath } = await readVerseFile(path);
        verseLspLog("editor", "readVerseFile ok", { path: canonicalPath, chars: content.length });

        if (cancelled) return;

        savedContentRef.current = content;



        verseLspLog("editor", "setupMonaco");
        const monacoApi = await setupMonaco();
        verseLspLog("editor", "setupMonaco ok");

        if (cancelled) return;

        monacoRef.current = monacoApi;

        setMonacoInstance(monacoApi);

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
        verseLspLog("editor", "model uri", { absPath, uri: uri.toString() });

        const existingModel = monacoApi.editor.getModel(uri);

        if (existingModel) {
          try {
            existingModel.dispose();
          } catch {
            /* model already torn down */
          }
        }

        const model = monacoApi.editor.createModel(content, "verse", uri);

        if (editorRef.current) {
          try {
            editorRef.current.dispose();
          } catch {
            /* layout race during recreate */
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

          fontWeight: "normal",

          tabSize: 4,

          insertSpaces: true,

          lineNumbers: "on",

          lineNumbersMinChars: 3,

          showFoldingControls: "mouseover",

          minimap: { enabled: minimapEnabled },

          wordWrap: "off",

          scrollBeyondLastLine: false,

          readOnly: locked,

          renderValidationDecorations: "on",

          glyphMargin: true,

          links: true,

          bracketPairColorization: { enabled: false },

          guides: { bracketPairs: false, indentation: true },

          smoothScrolling: true,

          cursorBlinking: "smooth",

          padding: { top: 8, bottom: 8 },

          // VS Code behavior: one result navigates (via the editor opener → app tab),
          // multiple results show the inline peek widget to choose from.
          gotoLocation: {
            multipleDefinitions: "peek",
            multipleDeclarations: "peek",
            multipleImplementations: "peek",
            multipleTypeDefinitions: "peek",
            multipleReferences: "peek",
          },

          contextmenu: true,

          hover: { enabled: true, delay: 350 },

          quickSuggestions: { other: true, comments: false, strings: false },

          // Don't fire a completion request on every keystroke while typing fast —
          // wait for a brief pause (verse-lsp completions are not instant).
          quickSuggestionsDelay: 300,

          // Only Verse LSP items and Verse snippets — never plain buffer words.
          wordBasedSuggestions: "off",

          // Pressing "." / ":" / "<" must pop the member list immediately —
          // no typing required (quickSuggestionsDelay only governs word typing).
          suggestOnTriggerCharacters: true,

          ...MONACO_EMBEDDED_OVERFLOW_OPTIONS,

        });

        editorRef.current = ed;

        setEditorInstance(ed);

        // Show the docs side-panel of the suggest widget by default — completion
        // docs come from the digest files and are the point of the list. Internal
        // API (no public option exists); cosmetic only, so failure is ignored.
        try {
          const sc = ed.getContribution("editor.contrib.suggestController") as unknown as {
            widget?: { value?: { _setDetailsVisible?: (v: boolean) => void } };
          } | null;
          sc?.widget?.value?._setDetailsVisible?.(true);
        } catch {
          /* internal API drifted — suggest details stay collapsed */
        }

        applyVerseMonacoTheme(
          monacoApi,
          appearanceRef.current.cssVars,
          appearanceRef.current.foundation,
          [ed],
        );

        registerEditor(path, ed);

        ed.onDidFocusEditorWidget(() => {
          setFocusedAskAiEditor(ed, normPath(path));
          setQuickOpenEditor(ed, normPath(path));
        });

        ed.onDidChangeModelContent(() => {

          if (playbackLockedRef.current || locked) return;

          clearHistoryPreview(path);

          setDirty(path, ed.getValue() !== savedContentRef.current);

        });



        ed.addCommand(monacoApi.KeyMod.CtrlCmd | monacoApi.KeyCode.KeyS, () => {

          void handleSaveRef.current();

        });



        if (!cancelled) {
          setReady(true);
          verseLspLog("editor", "init complete — editor ready", { path });
        }

      } catch (e) {

        if (!cancelled) {
          verseLspLogError("editor", "init failed", e);
          verseEditorLogError("editor", "init failed", e);
          const raw = e instanceof Error ? e.message : "Failed to load editor";
          const missing = raw.includes("Not a file");
          if (missing) {
            // External/project deletes never fire file_deleted — close the tab
            // ourselves so Problems + the top-bar badge cannot stick on a ghost.
            notifyMissingProjectFile(path);
            return;
          }
          const hint =
            raw.includes("onig.wasm") || raw.includes("TextMate") || raw.includes("grammar")
              ? `Verse highlighting failed: ${raw}`
              : raw;
          setError(hint);

        }

      }

    })();



    return () => {

      cancelled = true;
      verseLspLog("editor", "init cleanup", { path });

      unregisterEditor(path);

      const ed = editorRef.current;
      if (ed) clearQuickOpenEditor(ed, normPath(path));
      editorRef.current = null;
      setEditorInstance(null);
      setMonacoInstance(null);

      if (ed) {
        const model = ed.getModel();
        try {
          ed.dispose();
        } catch {
          /* layout race during maximize / unmount */
        }
        if (model && !model.isDisposed()) {
          try {
            model.dispose();
          } catch {
            /* model already torn down */
          }
        }
      }

    };

    // eslint-disable-next-line react-hooks/exhaustive-deps

  }, [path, projectRoot, appearanceReady]);



  useEffect(() => {

    editorRef.current?.updateOptions({ readOnly: playbackLocked });

  }, [playbackLocked]);



  // Colour the WHOLE file the moment it opens. Monaco skips its initial background
  // tokenization pass for an editor mounted in a briefly-hidden container, so without this
  // the document renders as default tokens (mtk1) until the user scrolls or edits. Runs once
  // the editor is ready (model attached, grammar loaded via setupMonaco).
  useEffect(() => {
    if (!editorInstance || !monacoInstance || !ready || error) return;
    forceFullTokenization(monacoInstance, editorInstance);
  }, [editorInstance, monacoInstance, ready, error]);



  useEffect(() => {

    const ed = editorRef.current;

    if (!ed) return;

    try {

      if (!ed.getModel()?.isDisposed()) {

        ed.updateOptions({ fontSize: monacoFontSizeForZoom(zoom) });

      }

    } catch {

      /* disposed */

    }

  }, [zoom]);



  useEffect(() => {

    const ed = editorRef.current;

    if (!ed) return;

    try {

      if (!ed.getModel()?.isDisposed()) {

        ed.updateOptions({ minimap: { enabled: minimapEnabled } });

      }

    } catch {

      /* disposed */

    }

  }, [minimapEnabled, editorInstance]);



  useEffect(() => {

    const ed = editorRef.current;

    const monacoApi = monacoRef.current;

    if (!ed || !monacoApi) return;



    const runAction = (id: string, label: string) => () => {
      verseLspLog("editor", `keybinding ${label}`, { action: id });
      try {
        if (ed.getModel()?.isDisposed()) return;
        void ed.getAction(id)?.run();
      } catch {
        /* editor disposed during window resize / tab switch */
      }
    };



    ed.addCommand(monacoApi.KeyCode.F12, runAction("editor.action.revealDefinition", "F12 definition"));

    ed.addCommand(

      monacoApi.KeyMod.Shift | monacoApi.KeyCode.F12,

      runAction("editor.action.goToReferences", "Shift+F12 references"),

    );

    ed.addCommand(

      monacoApi.KeyMod.CtrlCmd | monacoApi.KeyCode.Space,

      runAction("editor.action.triggerSuggest", "Ctrl+Space completion"),

    );

    ed.addCommand(monacoApi.KeyCode.F2, runAction("editor.action.rename", "F2 rename"));

    installQuickOpenKeybindings(ed, monacoApi);

  }, [editorInstance, monacoInstance]);



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

  }, [cssVars, foundation, monacoInstance, appearanceReady, zoom]);



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

      setSaveError(null);

      setDirty(path, false);

      if (projectRoot && isVerseDiagnosticsAutoCheckEnabled()) {
        refreshOpenFileDiagnosticsNow(projectRoot, path);
      }

      bumpHistoryRefresh();

      return true;

    } catch (e) {

      verseLspLogError("editor", "save failed", e);

      // A failed save must never be silent: surface it in a banner so the user knows
      // their buffer never reached disk and can retry / copy their work out.
      setSaveError(e instanceof Error ? e.message : "Save failed");

      return false;

    } finally {

      setSaving(false);

    }

  }, [path, saving, setDirty, dirty, projectRoot, locked]);



  handleSaveRef.current = handleSave;

  const applyDiskContent = useCallback(
    (content: string) => {
      const ed = editorRef.current;
      if (!ed) return;
      const model = ed.getModel();
      if (!model || model.isDisposed()) return;
      savedContentRef.current = content;
      if (ed.getValue() !== content) {
        model.setValue(content);
      }
      setDirty(path, false);
      clearHistoryPreview(path);
    },
    [path, setDirty, clearHistoryPreview],
  );

  const handleExternalFileChange = useCallback(async () => {
    if (playbackLocked) return;
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
      // Disk matches what WE last wrote: this poll is the echo of our own save (the
      // mtime/size watcher can't tell our writes from an external tool's). The buffer
      // differs only because the user kept typing after the save fired — reloading here
      // would revert those edits, snap the cursor to line 1 (model.setValue resets the
      // position) and wipe the undo stack, forcing a recovery from the history panel.
      // Leave the buffer alone; the next real external change (disk ≠ our last save) still
      // reloads below.
      if (content === savedContentRef.current) return;
      const previousContent =
        current !== savedContentRef.current ? current : savedContentRef.current;
      await recordExternalFileChange(path, previousContent, content);
      applyDiskContent(content);
      bumpHistoryRefresh();
    } catch (e) {
      verseLspLogError("editor", "external file reload failed", e);
      // Deleted on disk while open (Explorer, another tool / project switch):
      // auto-close the tab — leaving it open keeps the LSP doc alive and
      // resurrects stale Problems in the top bar.
      if (e instanceof Error && e.message.includes("Not a file")) {
        notifyMissingProjectFile(path);
      }
    }
  }, [path, playbackLocked, applyDiskContent, setDirty]);

  useWatchProjectFile(path, () => void handleExternalFileChange(), {
    enabled: ready && !error && !playbackLocked,
  });

  useEffect(() => {
    if (!editorInstance || !ready || error) {
      unregisterEditorBridge(path);
      return;
    }
    const model = editorInstance.getModel();
    if (!model || model.isDisposed()) {
      unregisterEditorBridge(path);
      return;
    }
    registerEditorBridge(path, {
      editor: editorInstance,
      model,
      lspReady,
      markSaved: (content: string) => {
        savedContentRef.current = content;
        setDirty(path, false);
      },
    });
    return () => unregisterEditorBridge(path);
  }, [
    editorInstance,
    ready,
    error,
    path,
    lspReady,
    registerEditorBridge,
    unregisterEditorBridge,
    setDirty,
  ]);

  useEffect(() => {

    registerSaveHandler(path, handleSave);

    return () => unregisterSaveHandler(path);

  }, [path, handleSave, registerSaveHandler, unregisterSaveHandler]);



  const historyPreviewActive =
    historyPreview != null && normPath(historyPreview.path) === path;

  const isResizing = useMonacoEditorLayout(containerRef, editorRef, ready && !error);

  return (

    <div className={`verse-editor-host${isResizing ? " verse-editor-host--resizing" : ""}`}>

      {historyPreviewActive ? (
        <div className="verse-history-preview-banner no-drag">
          <span className="verse-history-preview-banner-text">
            Previewing <strong>{historyPreview.label}</strong>
            {historyPreview.changeCount > 0
              ? ` — ${historyPreview.changeCount} difference${historyPreview.changeCount === 1 ? "" : "s"} highlighted`
              : " — matches your current edits"}
            . Your unsaved changes are preserved.
          </span>
          <button
            type="button"
            className="verse-history-preview-banner-btn"
            onClick={() => clearHistoryPreview(path)}
          >
            Dismiss preview
          </button>
        </div>
      ) : null}

      {saveError ? (
        <div className="verse-save-error-banner no-drag" role="alert">
          <span className="verse-save-error-banner-text">
            Couldn’t save {path.split("/").pop()}: {saveError}
          </span>
          <button
            type="button"
            className="verse-save-error-banner-btn"
            onClick={() => setSaveError(null)}
          >
            Dismiss
          </button>
        </div>
      ) : null}

      {!ready && !error && (

        <div className="ui-status-muted-shrink">

          Loading {path}…

        </div>

      )}

      {error && (

        <div className="ui-status-error-shrink">{error}</div>

      )}

      <div ref={zoomHostRef} className="verse-editor-body">
        <ReplayOverlay path={path} controller={replayController} />
        <div
          ref={containerRef}
          className={`verse-editor-container${ready && !error ? " is-visible" : ""}`}
          data-no-translate
        />
        <div
          className={`ctrl-wheel-zoom-indicator${indicatorVisible ? " ctrl-wheel-zoom-indicator--visible" : ""}`}
          aria-live="polite"
          aria-hidden={!indicatorVisible}
        >
          {formatZoomPercent(zoom)}
        </div>
      </div>

    </div>

  );

}


