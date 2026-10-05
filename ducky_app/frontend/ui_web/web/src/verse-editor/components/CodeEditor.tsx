import { useEffect, useRef, useState } from "react";

import type { editor } from "monaco-editor";

import { useAppearanceOptional } from "../../theme/AppearanceContext";
import { checkJsModel, codeModelUri, DUCKY_MARKER_OWNER, serviceMarkers, setDuckyLibs, toMonacoMarkers, type CodeMarker } from "../monaco/duckyJs";

/** How long typing rests before the type check runs again. */
const TYPE_CHECK_DELAY_MS = 500;
import { MONACO_EMBEDDED_OVERFLOW_OPTIONS } from "../monaco/embeddedEditorOverflow";
import { applyMonacoEditorFont, ensureMonacoFontReady } from "../monaco/resolveMonacoFontFamily";
import { setupMonaco } from "../monaco/setupMonaco";
import { applyVerseMonacoTheme } from "../monaco/verseTheme";

export type { CodeMarker } from "../monaco/duckyJs";

type Monaco = typeof import("monaco-editor");

export interface CodeEditorProps {
  value: string;
  onChange?: (value: string) => void;
  readOnly?: boolean;
  /** Names the model: one per workflow node ("<workflow>/<node>"). */
  path: string;
  /** Accessible name of the editor. */
  label: string;
  /** Problems this app found (the check, the last run, Test), drawn inline. */
  markers?: CodeMarker[];
  /** Module-scoped declarations for the JavaScript service (`declare module "ducky"`); they
   *  are shared by every JavaScript model, so leaving this out keeps the ones there. */
  libs?: string[];
  /** Scroll to a line and put the cursor there; a new nonce asks again. */
  reveal?: { line: number; nonce: number } | null;
  onFocus?: () => void;
  onBlur?: () => void;
  /** What Monaco's own JavaScript check found (only with `// @ts-check`). */
  onServiceMarkers?: (markers: CodeMarker[]) => void;
  /** Force the plain text box (phones use it anyway). */
  plain?: boolean;
  className?: string;
}

/** Phones (coarse pointer) and environments without a real layout engine get a text box. */
export function prefersPlainEditor(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return true;
  try { return window.matchMedia("(pointer: coarse)").matches; } catch { return true; }
}

/** A JavaScript editor: Monaco on a desktop, a plain text box on a phone. */
export function CodeEditor(props: CodeEditorProps) {
  const [plain] = useState(() => props.plain || prefersPlainEditor());
  return plain || props.plain ? <PlainCodeEditor {...props} /> : <MonacoCodeEditor {...props} />;
}

function offsetOfLine(text: string, line: number): number {
  let at = 0;
  for (let current = 1; current < line; current++) {
    const next = text.indexOf("\n", at);
    if (next < 0) return text.length;
    at = next + 1;
  }
  return at;
}

function PlainCodeEditor({ value, onChange, readOnly, label, reveal, onFocus, onBlur, className }: CodeEditorProps) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const box = ref.current;
    if (!box || !reveal) return;
    const at = offsetOfLine(box.value, reveal.line);
    try { box.setSelectionRange(at, at); } catch { /* not focusable yet */ }
    const lineHeight = parseFloat(window.getComputedStyle(box).lineHeight) || 18;
    box.scrollTop = Math.max(0, (reveal.line - 3) * lineHeight);
  }, [reveal?.nonce]);
  return <textarea ref={ref} className={`aw-code-plain${className ? ` ${className}` : ""}`} aria-label={label} value={value} readOnly={readOnly}
    spellCheck={false} autoCapitalize="off" autoCorrect="off" wrap="off" data-gramm="false"
    onChange={(event) => onChange?.(event.target.value)} onFocus={onFocus} onBlur={onBlur}
    onKeyDown={(event) => {
      // Tab indents instead of leaving the box (Shift+Tab still leaves it).
      if (event.key !== "Tab" || event.shiftKey || readOnly) return;
      event.preventDefault();
      const box = event.currentTarget;
      const start = box.selectionStart;
      const next = `${box.value.slice(0, start)}  ${box.value.slice(box.selectionEnd)}`;
      onChange?.(next);
      window.requestAnimationFrame(() => { try { box.setSelectionRange(start + 2, start + 2); } catch { /* gone */ } });
    }} />;
}

function MonacoCodeEditor({ value, onChange, readOnly, path, label, markers, libs, reveal, onFocus, onBlur, onServiceMarkers, className }: CodeEditorProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<Monaco | null>(null);
  const appearance = useAppearanceOptional();
  const latest = useRef({ value, onChange, onFocus, onBlur, onServiceMarkers, appearance, readOnly });
  latest.current = { value, onChange, onFocus, onBlur, onServiceMarkers, appearance, readOnly };
  const suppress = useRef(false);
  const typeCheck = useRef(() => {});
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const disposers: Array<() => void> = [];
    void (async () => {
      try {
        const monaco = await setupMonaco();
        const container = containerRef.current;
        if (cancelled || !container) return;
        monacoRef.current = monaco;
        const look = latest.current.appearance;
        if (look) applyVerseMonacoTheme(monaco, look.cssVars, look.foundation);
        const fontFamily = look ? await ensureMonacoFontReady(look.cssVars, 13) : undefined;
        if (cancelled) return;
        const uri = codeModelUri(monaco, path);
        monaco.editor.getModel(uri)?.dispose();
        const model = monaco.editor.createModel(latest.current.value, "javascript", uri);
        const ed = monaco.editor.create(container, {
          model,
          theme: "verse-dark",
          readOnly: !!readOnly,
          ariaLabel: label,
          automaticLayout: true,
          fontSize: 13,
          lineHeight: 20,
          ...(fontFamily ? { fontFamily } : {}),
          fontLigatures: false,
          tabSize: 2,
          insertSpaces: true,
          lineNumbers: "on",
          glyphMargin: false,
          folding: true,
          lineDecorationsWidth: 6,
          lineNumbersMinChars: 3,
          minimap: { enabled: false },
          wordWrap: "off",
          scrollBeyondLastLine: false,
          overviewRulerLanes: 2,
          hideCursorInOverviewRuler: true,
          scrollbar: { verticalScrollbarSize: 8, horizontalScrollbarSize: 8, alwaysConsumeMouseWheel: false },
          padding: { top: 8, bottom: 8 },
          contextmenu: true,
          ...MONACO_EMBEDDED_OVERFLOW_OPTIONS,
        });
        editorRef.current = ed;
        // Undone in reverse: the listeners, then the editor, then its model.
        disposers.push(() => model.dispose(), () => ed.dispose());
        // The type check (read-only code is only shown, not checked).
        let checkTimer = 0;
        typeCheck.current = () => {
          window.clearTimeout(checkTimer);
          if (latest.current.readOnly) return;
          checkTimer = window.setTimeout(() => { if (!model.isDisposed()) void checkJsModel(monaco, model).catch(() => undefined); }, TYPE_CHECK_DELAY_MS);
        };
        disposers.push(() => { window.clearTimeout(checkTimer); typeCheck.current = () => {}; });
        typeCheck.current();
        for (const sub of [
          ed.onDidChangeModelContent(() => { typeCheck.current(); if (!suppress.current) latest.current.onChange?.(ed.getValue()); }),
          ed.onDidFocusEditorText(() => latest.current.onFocus?.()),
          ed.onDidBlurEditorText(() => latest.current.onBlur?.()),
          monaco.editor.onDidChangeMarkers((uris) => {
            if (uris.some((changed) => changed.toString() === uri.toString())) latest.current.onServiceMarkers?.(serviceMarkers(monaco, model));
          }),
        ]) disposers.push(() => sub.dispose());
        setReady(true);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
      editorRef.current = null;
      setReady(false);
      for (const dispose of disposers.reverse()) { try { dispose(); } catch { /* already disposed */ } }
    };
  }, [path]);

  // The text from outside (undo on the canvas, a save): as an edit, so Ctrl+Z inside still works.
  useEffect(() => {
    const ed = editorRef.current;
    const model = ed?.getModel();
    if (!ed || !model || !ready || model.getValue() === value) return;
    suppress.current = true;
    try { model.pushEditOperations([], [{ range: model.getFullModelRange(), text: value }], () => null); } finally { suppress.current = false; }
  }, [value, ready]);

  useEffect(() => { editorRef.current?.updateOptions({ readOnly: !!readOnly }); }, [readOnly, ready]);

  useEffect(() => {
    const monaco = monacoRef.current;
    // The libs are shared by every model: an editor that brings none leaves them as they are.
    if (!monaco || !ready || !libs) return;
    setDuckyLibs(monaco, libs);
    typeCheck.current();
  }, [libs?.join("\u0000"), ready]);

  useEffect(() => {
    const monaco = monacoRef.current;
    const model = editorRef.current?.getModel();
    if (!monaco || !model || !ready) return;
    monaco.editor.setModelMarkers(model, DUCKY_MARKER_OWNER, toMonacoMarkers(monaco, model, markers || []));
  }, [markers, ready, value]);

  useEffect(() => {
    const ed = editorRef.current;
    if (!ed || !ready || !reveal) return;
    ed.revealLineInCenter(reveal.line);
    ed.setPosition({ lineNumber: reveal.line, column: 1 });
  }, [reveal?.nonce, ready]);

  useEffect(() => {
    const monaco = monacoRef.current;
    const ed = editorRef.current;
    if (!monaco || !ed || !ready || !appearance) return;
    applyVerseMonacoTheme(monaco, appearance.cssVars, appearance.foundation, [ed]);
    void ensureMonacoFontReady(appearance.cssVars, 13).then((fontFamily) => applyMonacoEditorFont(ed, fontFamily, 13));
  }, [appearance?.cssVars, appearance?.foundation, ready]);

  if (failed) return <PlainCodeEditor value={value} onChange={onChange} readOnly={readOnly} path={path} label={label} reveal={reveal} onFocus={onFocus} onBlur={onBlur} className={className} />;
  return <div ref={containerRef} className={`aw-code-monaco${className ? ` ${className}` : ""}`} />;
}

/** Compare: the built-in's code against this node's, one column with the changes marked. */
export function CodeDiff({ original, modified, path, plain }: { original: string; modified: string; path: string; plain?: boolean }) {
  const [usePlain] = useState(() => plain || prefersPlainEditor());
  return usePlain || plain ? <PlainDiff original={original} modified={modified} /> : <MonacoDiff original={original} modified={modified} path={path} />;
}

type DiffLine = { kind: " " | "+" | "-"; text: string };

/** A line diff (longest common subsequence); very long code compares line by line in order. */
export function diffLines(original: string, modified: string): DiffLine[] {
  const a = original.split("\n");
  const b = modified.split("\n");
  if (a.length * b.length > 4_000_000) {
    return [...a.map((text) => ({ kind: "-" as const, text })), ...b.map((text) => ({ kind: "+" as const, text }))];
  }
  const width = b.length + 1;
  const table = new Uint32Array((a.length + 1) * width);
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      table[i * width + j] = a[i] === b[j] ? table[(i + 1) * width + j + 1] + 1 : Math.max(table[(i + 1) * width + j], table[i * width + j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0, j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { out.push({ kind: " ", text: a[i] }); i++; j++; }
    else if (table[(i + 1) * width + j] >= table[i * width + j + 1]) out.push({ kind: "-", text: a[i++] });
    else out.push({ kind: "+", text: b[j++] });
  }
  while (i < a.length) out.push({ kind: "-", text: a[i++] });
  while (j < b.length) out.push({ kind: "+", text: b[j++] });
  return out;
}

function PlainDiff({ original, modified }: { original: string; modified: string }) {
  const lines = diffLines(original, modified);
  return <pre className="aw-code-diff-plain" aria-label="Changes from the built-in">
    {lines.map((line, index) => <span key={index} className={line.kind === "+" ? "is-added" : line.kind === "-" ? "is-removed" : undefined}>{line.kind} {line.text}{"\n"}</span>)}
  </pre>;
}

function MonacoDiff({ original, modified, path }: { original: string; modified: string; path: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  const texts = useRef({ original, modified });
  texts.current = { original, modified };
  const models = useRef<{ original: editor.ITextModel; modified: editor.ITextModel } | null>(null);
  useEffect(() => {
    let cancelled = false;
    let dispose = () => {};
    void (async () => {
      try {
        const monaco = await setupMonaco();
        const container = containerRef.current;
        if (cancelled || !container) return;
        const left = monaco.editor.createModel(texts.current.original, "javascript", codeModelUri(monaco, `${path}.builtin`));
        const right = monaco.editor.createModel(texts.current.modified, "javascript", codeModelUri(monaco, `${path}.compare`));
        const diff = monaco.editor.createDiffEditor(container, {
          readOnly: true,
          originalEditable: false,
          renderSideBySide: false,
          automaticLayout: true,
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          fontSize: 13,
          lineHeight: 20,
          theme: "verse-dark",
          ...MONACO_EMBEDDED_OVERFLOW_OPTIONS,
        });
        diff.setModel({ original: left, modified: right });
        models.current = { original: left, modified: right };
        dispose = () => { models.current = null; diff.dispose(); left.dispose(); right.dispose(); };
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => { cancelled = true; try { dispose(); } catch { /* already disposed */ } };
  }, [path]);
  useEffect(() => {
    const pair = models.current;
    if (!pair) return;
    if (pair.original.getValue() !== original) pair.original.setValue(original);
    if (pair.modified.getValue() !== modified) pair.modified.setValue(modified);
  }, [original, modified]);
  if (failed) return <PlainDiff original={original} modified={modified} />;
  return <div ref={containerRef} className="aw-code-monaco aw-code-diff" aria-label="Changes from the built-in" />;
}
