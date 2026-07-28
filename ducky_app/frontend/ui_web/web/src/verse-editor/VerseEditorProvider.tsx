import {

  createContext,

  useCallback,

  useContext,

  useEffect,

  useMemo,

  useRef,

  useState,

  type ReactNode,

} from "react";

import type { FileEditData } from "../types/panel";
import { checkFileAfterAgentWrite } from "./diagnostics/checkFileAfterAgentWrite";
import { EditorActionQueue } from "./queue/EditorActionQueue";
import { ReplayController } from "./replay/ReplayController";

import { useVerseEditorSync } from "./hooks/useVerseEditorSync";

import { releaseVerseLspSession } from "./lsp/verseLspSession";

import { fileDiagnosticRegistry, type FileDiagnosticSummary } from "./lsp/fileDiagnosticRegistry";

import { useVerseDiagnosticsSync } from "./hooks/useVerseDiagnosticsSync";

import { normPath, registryKey } from "./utils/isVerseFile";

import {
  applyHistoryPreviewDecorations,
  clearHistoryPreviewDecorations,
  revealFirstHistoryChange,
} from "./history/historyPreviewDecorations";
export interface HistoryPreviewState {
  path: string;
  entryId: string;
  label: string;
  changeCount: number;
}



interface VerseEditorContextValue {

  queue: EditorActionQueue;

  replayController: ReplayController;

  /** Start a stepped, controllable replay of a past agent edit (replay button). */
  startReplay: (edit: FileEditData) => void;

  dirtyPaths: Set<string>;

  setDirty: (path: string, dirty: boolean) => void;

  playbackLocked: boolean;

  registerEditor: (path: string, editor: import("monaco-editor").editor.IStandaloneCodeEditor) => void;

  unregisterEditor: (path: string) => void;

  registerSaveHandler: (path: string, handler: () => Promise<boolean>) => void;

  unregisterSaveHandler: (path: string) => void;

  savePath: (path: string) => Promise<boolean>;

  isPathDirty: (path: string) => boolean;

  restoreFromHistory: (path: string, content: string, entryId?: string) => boolean;

  getEditorContent: (path: string) => string | undefined;

  historyPreview: HistoryPreviewState | null;

  previewHistoryEntry: (
    path: string,
    entryId: string,
    historicalContent: string,
    label: string,
  ) => boolean;

  clearHistoryPreview: (path?: string) => void;

  openFileAt: (path: string, name: string, options?: { activate?: boolean }) => void;

  requestReveal: (path: string, line: number, column: number) => void;

  revealIfOpen: (path: string, line: number, column: number) => boolean;

  getFileDiagnosticSummary: (path: string) => FileDiagnosticSummary | undefined;

  getFolderDiagnosticSummary: (path: string) => FileDiagnosticSummary;

  isAgentActiveOnPath: (path: string) => boolean;

  registerEditorBridge: (
    path: string,
    bridge: {
      editor: import("monaco-editor").editor.IStandaloneCodeEditor;
      model: import("monaco-editor").editor.ITextModel;
      lspReady: boolean;
      markSaved?: (content: string) => void;
    },
  ) => void;

  unregisterEditorBridge: (path: string) => void;

  getEditorBridge: (path: string) => {
    editor: import("monaco-editor").editor.IStandaloneCodeEditor;
    model: import("monaco-editor").editor.ITextModel;
    lspReady: boolean;
    markSaved?: (content: string) => void;
  } | null;

  historyRefreshKey: number;

  bumpHistoryRefresh: () => void;

}



const VerseEditorContext = createContext<VerseEditorContextValue | null>(null);



export function useVerseEditor(): VerseEditorContextValue {

  const ctx = useContext(VerseEditorContext);

  if (!ctx) throw new Error("useVerseEditor must be used within VerseEditorProvider");

  return ctx;

}



export function useVerseEditorOptional(): VerseEditorContextValue | null {

  return useContext(VerseEditorContext);

}



interface VerseEditorProviderProps {

  children: ReactNode;

  onOpenFile: (path: string, name: string, options?: { activate?: boolean }) => void;

  /** Queue (agent follow-code) opens only — user opens via openFileAt keep onOpenFile. */
  onAgentOpenFile?: (path: string, name: string, options?: { activate?: boolean }) => void;

  onRequestSplit?: () => void;

  onFileSync?: (path: string) => void;

  projectPath?: string;

}



export function VerseEditorProvider({

  children,

  onOpenFile,

  onAgentOpenFile,

  onRequestSplit,

  onFileSync,

  projectPath = "",

}: VerseEditorProviderProps) {

  const [dirtyPaths, setDirtyPaths] = useState<Set<string>>(() => new Set());

  const [playbackLocked, setPlaybackLocked] = useState(false);

  const [diagnosticRevision, setDiagnosticRevision] = useState(0);

  const [historyPreview, setHistoryPreview] = useState<HistoryPreviewState | null>(null);

  // Mirror of historyPreview for callbacks that must stay identity-stable, plus
  // a listener that dismisses the preview as soon as the buffer changes —
  // stale line-anchored overlays render garbage over edited text otherwise.
  const historyPreviewRef = useRef<HistoryPreviewState | null>(null);
  const historyEditGuardRef = useRef<{ path: string; dispose: () => void } | null>(null);

  const editorBridgesRef = useRef(
    new Map<
      string,
      {
        editor: import("monaco-editor").editor.IStandaloneCodeEditor;
        model: import("monaco-editor").editor.ITextModel;
        lspReady: boolean;
        markSaved?: (content: string) => void;
      }
    >(),
  );
  const [bridgeRevision, setBridgeRevision] = useState(0);
  const [historyRefreshKey, setHistoryRefreshKey] = useState(0);

  const bumpHistoryRefresh = useCallback(() => setHistoryRefreshKey((n) => n + 1), []);

  const registerEditorBridge = useCallback(
    (
      path: string,
      bridge: {
        editor: import("monaco-editor").editor.IStandaloneCodeEditor;
        model: import("monaco-editor").editor.ITextModel;
        lspReady: boolean;
        markSaved?: (content: string) => void;
      },
    ) => {
      const key = normPath(path);
      const prev = editorBridgesRef.current.get(key);
      if (
        prev?.editor === bridge.editor &&
        prev?.model === bridge.model &&
        prev?.lspReady === bridge.lspReady &&
        prev?.markSaved === bridge.markSaved
      ) {
        return;
      }
      editorBridgesRef.current.set(key, bridge);
      setBridgeRevision((n) => n + 1);
    },
    [],
  );

  const unregisterEditorBridge = useCallback((path: string) => {
    editorBridgesRef.current.delete(normPath(path));
    setBridgeRevision((n) => n + 1);
  }, []);

  const getEditorBridge = useCallback(
    (path: string) => {
      void bridgeRevision;
      return editorBridgesRef.current.get(normPath(path)) ?? null;
    },
    [bridgeRevision],
  );

  const bumpDiagnostics = useCallback(() => setDiagnosticRevision((n) => n + 1), []);

  useVerseDiagnosticsSync(bumpDiagnostics);

  const dirtyRef = useRef(dirtyPaths);

  dirtyRef.current = dirtyPaths;

  const pendingRevealRef = useRef(new Map<string, { line: number; column: number }>());

  const saveHandlersRef = useRef(new Map<string, () => Promise<boolean>>());



  const setDirty = useCallback((path: string, dirty: boolean) => {

    const key = normPath(path);

    setDirtyPaths((prev) => {

      // Keystrokes call this with an unchanged value — returning prev keeps the
      // context value stable so the whole editor UI doesn't re-render per key.
      if (prev.has(key) === dirty) return prev;

      const next = new Set(prev);

      if (dirty) next.add(key);

      else next.delete(key);

      return next;

    });

  }, []);



  const projectPathRef = useRef(projectPath);

  projectPathRef.current = projectPath;

  const queueRef = useRef<EditorActionQueue | null>(null);



  const queue = useMemo(

    () => {

      const q = new EditorActionQueue({

        onOpenFile: onAgentOpenFile ?? onOpenFile,

        onRequestSplit,

        onFileSync,

        onPlaybackChange: setPlaybackLocked,

        onDirty: setDirty,

        onMarkSaved: (path, content) => {
          const bridge = editorBridgesRef.current.get(normPath(path));
          bridge?.markSaved?.(content);
          setDirty(path, false);
        },

        isPathDirty: (path) => dirtyRef.current.has(normPath(path)),

        onAgentWriteComplete: (path) => {

          const root = projectPathRef.current.trim();

          if (!root) return;

          void checkFileAfterAgentWrite(root, path, (p) =>

            queueRef.current?.getEditor(p)?.getModel()?.getValue(),

          );

        },

      });

      queueRef.current = q;

      return q;

    },

    [onOpenFile, onAgentOpenFile, onRequestSplit, onFileSync, setDirty],

  );



  useVerseEditorSync(queue);

  // One verse-lsp session per project while the editor UI is mounted — not per open file tab.
  useEffect(() => () => {
    releaseVerseLspSession();
  }, []);

  // Replay walkthroughs open beside the chat, same as agent follow-code.
  const replayController = useMemo(
    () => new ReplayController(queue, onAgentOpenFile ?? onOpenFile, onRequestSplit),
    [queue, onAgentOpenFile, onOpenFile, onRequestSplit],
  );

  // Tear down any in-flight replay (timers + decorations) when the controller is
  // replaced or the editor UI unmounts.
  useEffect(() => () => replayController.stop(), [replayController]);

  const startReplay = useCallback(
    (edit: FileEditData) => replayController.startFromEdit(edit),
    [replayController],
  );

  const registerSaveHandler = useCallback((path: string, handler: () => Promise<boolean>) => {

    saveHandlersRef.current.set(normPath(path), handler);

  }, []);



  const unregisterSaveHandler = useCallback((path: string) => {

    saveHandlersRef.current.delete(normPath(path));

  }, []);



  const savePath = useCallback(async (path: string): Promise<boolean> => {

    const key = normPath(path);

    const handler = saveHandlersRef.current.get(key);

    if (handler) return handler();

    return !dirtyRef.current.has(key);

  }, []);



  const isPathDirty = useCallback((path: string) => dirtyPaths.has(normPath(path)), [dirtyPaths]);

  const clearHistoryPreview = useCallback(
    (path?: string) => {
      const key = path ? normPath(path) : historyPreviewRef.current?.path;
      if (!key) return;
      if (historyEditGuardRef.current?.path === key) {
        historyEditGuardRef.current.dispose();
        historyEditGuardRef.current = null;
      }
      clearHistoryPreviewDecorations(key, queue.getEditor(key));
      if (historyPreviewRef.current?.path === key) {
        historyPreviewRef.current = null;
        setHistoryPreview(null);
      }
    },
    [queue],
  );

  const previewHistoryEntry = useCallback(
    (path: string, entryId: string, historicalContent: string, label: string) => {
      const key = normPath(path);
      const ed = queue.getEditor(key);
      const model = ed?.getModel();
      if (!ed || !model) return false;

      // Only one preview at a time — drop a lingering preview on another file.
      const prev = historyPreviewRef.current;
      if (prev && prev.path !== key) clearHistoryPreview(prev.path);
      historyEditGuardRef.current?.dispose();

      const currentText = model.getValue();
      const changeCount = applyHistoryPreviewDecorations(key, ed, currentText, historicalContent);
      revealFirstHistoryChange(ed, currentText, historicalContent);

      const guard = ed.onDidChangeModelContent(() => clearHistoryPreview(key));
      historyEditGuardRef.current = { path: key, dispose: () => guard.dispose() };

      const next = { path: key, entryId, label, changeCount };
      historyPreviewRef.current = next;
      setHistoryPreview(next);
      return true;
    },
    [queue, clearHistoryPreview],
  );

  const restoreFromHistory = useCallback(
    (path: string, content: string, _entryId?: string) => {
      const key = normPath(path);
      const ed = queue.getEditor(key);
      const model = ed?.getModel();
      if (!ed || !model) return false;
      clearHistoryPreview(key);
      model.setValue(content);
      setDirty(key, true);
      ed.focus();
      return true;
    },
    [queue, clearHistoryPreview, setDirty],
  );

  const getEditorContent = useCallback(
    (path: string) => queue.getEditor(path)?.getModel()?.getValue(),
    [queue],
  );

  const openFileAt = useCallback(

    (path: string, name: string, options?: { activate?: boolean }) => {

      onOpenFile(path, name, options);

    },

    [onOpenFile],

  );



  const requestReveal = useCallback((path: string, line: number, column: number) => {

    const key = normPath(path);

    if (queue.revealIfOpen(key, line, column)) return;

    pendingRevealRef.current.set(key, { line, column });

  }, [queue]);



  const getFileDiagnosticSummary = useCallback(
    (path: string) => {
      void diagnosticRevision;
      return fileDiagnosticRegistry.getSummary(path);
    },
    [diagnosticRevision],
  );

  const getFolderDiagnosticSummary = useCallback(
    (path: string) => {
      void diagnosticRevision;
      return fileDiagnosticRegistry.getDescendantSummary(path);
    },
    [diagnosticRevision],
  );

  const isAgentActiveOnPath = useCallback(
    (path: string) => queue.isAgentActive(registryKey(path)),
    [queue],
  );

  const revealIfOpen = useCallback(
    (path: string, line: number, column: number) => queue.revealIfOpen(path, line, column),
    [queue],
  );



  const registerEditor = useCallback(

    (path: string, editor: import("monaco-editor").editor.IStandaloneCodeEditor) => {

      queue.registerEditor(path, editor);

      const key = normPath(path);

      const pending = pendingRevealRef.current.get(key);

      if (pending) {

        pendingRevealRef.current.delete(key);

        requestAnimationFrame(() => {

          editor.revealPositionInCenter({

            lineNumber: pending.line,

            column: pending.column,

          });

          editor.setPosition({ lineNumber: pending.line, column: pending.column });

          editor.focus();

        });

      }

    },

    [queue],

  );



  const unregisterEditor = useCallback(

    (path: string) => {

      const key = normPath(path);

      clearHistoryPreview(key);

      queue.unregisterEditor(path);

      unregisterSaveHandler(path);

    },

    [queue, clearHistoryPreview, unregisterSaveHandler],

  );



  const value = useMemo(

    () => ({

      queue,

      replayController,

      startReplay,

      dirtyPaths,

      setDirty,

      playbackLocked,

      registerEditor,

      unregisterEditor,

      registerSaveHandler,

      unregisterSaveHandler,

      savePath,

      isPathDirty,

      restoreFromHistory,

      getEditorContent,

      historyPreview,

      previewHistoryEntry,

      clearHistoryPreview,

      openFileAt,

      requestReveal,

      revealIfOpen,

      getFileDiagnosticSummary,

      getFolderDiagnosticSummary,

      isAgentActiveOnPath,

      registerEditorBridge,

      unregisterEditorBridge,

      getEditorBridge,

      historyRefreshKey,

      bumpHistoryRefresh,

    }),

    [

      queue,

      replayController,

      startReplay,

      dirtyPaths,

      setDirty,

      playbackLocked,

      registerEditor,

      unregisterEditor,

      registerSaveHandler,

      unregisterSaveHandler,

      savePath,

      isPathDirty,

      restoreFromHistory,

      getEditorContent,

      historyPreview,

      previewHistoryEntry,

      clearHistoryPreview,

      openFileAt,

      requestReveal,

      revealIfOpen,

      getFileDiagnosticSummary,

      getFolderDiagnosticSummary,

      isAgentActiveOnPath,

      registerEditorBridge,

      unregisterEditorBridge,

      getEditorBridge,

      historyRefreshKey,

      bumpHistoryRefresh,

    ],

  );



  return (
    <VerseEditorContext.Provider value={value}>
      {children}
    </VerseEditorContext.Provider>
  );

}


