import { useEffect, useId, useRef, useState, type CSSProperties } from "react";
import type { editor } from "monaco-editor";

import { Icons } from "../../icons/Icons";
import { useAppearanceOptional } from "../../theme/AppearanceContext";
import { copyText } from "../../utils/copyText";
import { MONACO_EMBEDDED_OVERFLOW_OPTIONS } from "../monaco/embeddedEditorOverflow";
import { forceFullTokenization } from "../monaco/forceFullTokenization";
import {
  applyMonacoEditorFont,
  ensureMonacoFontReady,
  MONACO_EDITOR_FONT_SIZE,
} from "../monaco/resolveMonacoFontFamily";
import { setupMonaco } from "../monaco/setupMonaco";
import { useMonacoEditorLayout } from "../monaco/useMonacoEditorLayout";
import { applyVerseMonacoTheme } from "../monaco/verseTheme";

type LiveCodePreviewProps = {
  value: string;
  language: string;
  fill?: boolean;
  className?: string;
  /** Static highlighted text until clicked; the Monaco editor is made on the first click. */
  staticUntilClick?: boolean;
  /** Plain text, never an editor (a block still streaming). */
  plain?: boolean;
};

// Same box as the editor (21 px lines, 8 px padding, 378 px cap) so heights never jump.
const STATIC_STYLE: CSSProperties = {
  boxSizing: "border-box",
  padding: "8px 14px",
  lineHeight: "21px",
  fontSize: MONACO_EDITOR_FONT_SIZE,
  maxHeight: 378,
  overflow: "hidden",
  whiteSpace: "pre",
  overflowWrap: "normal",
};

// ponytail: unbounded text+language cache; add an LRU if long sessions grow it.
const colorizeCache = new Map<string, string>();
const colorizing = new Map<string, Promise<string>>();

function useColorized(value: string, language: string, enabled: boolean): string | null {
  const appearance = useAppearanceOptional();
  const key = `${language}\n${value}`;
  const [html, setHtml] = useState<string | null>(() => colorizeCache.get(key) ?? null);
  const ready = Boolean(appearance?.appearanceReady);
  useEffect(() => {
    if (!enabled) return;
    const hit = colorizeCache.get(key);
    if (hit !== undefined) {
      setHtml(hit);
      return;
    }
    setHtml(null);
    if (!ready || !appearance) return;
    let cancelled = false;
    let job = colorizing.get(key);
    if (!job) {
      job = (async () => {
        const monaco = await setupMonaco();
        // The theme's token classes (mtkN) are global once it is set.
        applyVerseMonacoTheme(monaco, appearance.cssVars, appearance.foundation);
        const out = await monaco.editor.colorize(value, language || "plaintext", {});
        colorizeCache.set(key, out);
        return out;
      })().finally(() => colorizing.delete(key));
      colorizing.set(key, job);
    }
    job.then((out) => { if (!cancelled) setHtml(out); }, () => { /* plain text stays */ });
    return () => { cancelled = true; };
  }, [enabled, key, ready, appearance, value, language]);
  return enabled ? html : null;
}

/**
 * Read-only Monaco using the same language/theme stack as the project editor.
 * Settings Verse preview and chat fences both mount this — no homemade highlighter.
 */
export function LiveCodePreview({ value, language, fill, className, staticUntilClick, plain }: LiveCodePreviewProps) {
  const appearance = useAppearanceOptional();
  const containerRef = useRef<HTMLDivElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<typeof import("monaco-editor") | null>(null);
  const appearanceRef = useRef(appearance);
  appearanceRef.current = appearance;
  const instanceId = useId().replace(/[^a-zA-Z0-9]/g, "") || "preview";
  const [ready, setReady] = useState(false);
  const [unlocked, setUnlocked] = useState(false);
  const [hint, setHint] = useState(false);
  const [copied, setCopied] = useState(false);
  const [activated, setActivated] = useState(!staticUntilClick);
  const isStatic = Boolean(plain) || !activated;
  const canMount = Boolean(appearance?.appearanceReady) && !isStatic;
  const staticHtml = useColorized(value, language, Boolean(staticUntilClick) && !plain && !activated);
  const lockScroll = !fill;

  useMonacoEditorLayout(containerRef, editorRef, ready);

  useEffect(() => {
    if (!canMount) return;
    let cancelled = false;

    void (async () => {
      const container = containerRef.current;
      const theme = appearanceRef.current;
      if (!container || !theme) return;

      const monaco = await setupMonaco();
      if (cancelled) return;
      monacoRef.current = monaco;
      applyVerseMonacoTheme(monaco, theme.cssVars, theme.foundation);

      const fontFamily = await ensureMonacoFontReady(theme.cssVars);
      if (cancelled) return;

      const uri = monaco.Uri.parse(`inmemory://live-preview/${instanceId}/${language || "txt"}`);
      const existing = monaco.editor.getModel(uri);
      if (existing) {
        try {
          existing.dispose();
        } catch {
          /* already torn down */
        }
      }
      const model = monaco.editor.createModel(value, language || "plaintext", uri);

      const ed = monaco.editor.create(container, {
        model,
        theme: "verse-dark",
        readOnly: true,
        domReadOnly: true,
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        lineNumbers: "on",
        lineNumbersMinChars: 3,
        folding: false,
        glyphMargin: false,
        fontSize: MONACO_EDITOR_FONT_SIZE,
        lineHeight: 21,
        fontFamily,
        fontLigatures: false,
        fontWeight: "normal",
        mouseWheelZoom: false,
        padding: { top: 8, bottom: 8 },
        overviewRulerLanes: 0,
        hideCursorInOverviewRuler: true,
        overviewRulerBorder: false,
        scrollbar: { verticalScrollbarSize: 8, horizontalScrollbarSize: 8 },
        renderLineHighlight: "none",
        matchBrackets: "always",
        contextmenu: false,
        ...MONACO_EMBEDDED_OVERFLOW_OPTIONS,
      });

      editorRef.current = ed;
      if (!cancelled) {
        setReady(true);
        applyVerseMonacoTheme(monaco, theme.cssVars, theme.foundation, [ed]);
        forceFullTokenization(monaco, ed);
      }
    })();

    return () => {
      cancelled = true;
      setReady(false);
      const ed = editorRef.current;
      editorRef.current = null;
      if (ed) {
        const model = ed.getModel();
        try {
          ed.dispose();
        } catch {
          /* disposed */
        }
        try {
          model?.dispose();
        } catch {
          /* disposed */
        }
      }
      monacoRef.current = null;
    };
    // Remount when the fence language changes so Monaco gets a fresh model/grammar.

  }, [canMount, language, instanceId]);

  useEffect(() => {
    const ed = editorRef.current;
    if (!ed || !ready) return;
    if (ed.getValue() === value) return;
    ed.setValue(value);
    if (monacoRef.current) forceFullTokenization(monacoRef.current, ed);
  }, [value, ready]);

  useEffect(() => {
    const monaco = monacoRef.current;
    const ed = editorRef.current;
    const theme = appearance;
    if (!monaco || !ed || !ready || !theme) return;
    applyVerseMonacoTheme(monaco, theme.cssVars, theme.foundation, [ed]);
    void ensureMonacoFontReady(theme.cssVars).then((fontFamily) => {
      applyMonacoEditorFont(ed, fontFamily);
    });
  }, [appearance, ready]);

  const lines = Math.max(value.split("\n").length, 1);
  useEffect(() => {
    wrapRef.current?.style.setProperty("--live-code-lines", String(lines));
  }, [lines]);

  useEffect(() => {
    if (!unlocked) return;
    const onDown = (e: MouseEvent) => {
      const wrap = wrapRef.current;
      if (wrap && !wrap.contains(e.target as Node)) {
        setUnlocked(false);
        setHint(false);
        setCopied(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [unlocked]);

  useEffect(() => {
    if (!ready) return;
    editorRef.current?.layout();
  }, [ready, unlocked]);

  return (
    <div
      ref={wrapRef}
      className={`live-code-preview${fill ? " live-code-preview--fill" : ""}${canMount ? " live-code-preview--boot" : ""}${ready ? " live-code-preview--ready" : ""}${unlocked ? " live-code-preview--active" : ""}${className ? ` ${className}` : ""}`}
    >
      {isStatic ? (
        staticHtml !== null ? (
          <pre
            className="live-code-preview-source live-code-preview-static"
            style={STATIC_STYLE}
            // Monaco's colorize escapes the text; only its token spans are markup.
            dangerouslySetInnerHTML={{ __html: staticHtml }}
          />
        ) : (
          <pre className="live-code-preview-source live-code-preview-static" style={STATIC_STYLE}>
            <code>{value}</code>
          </pre>
        )
      ) : (
        <pre className="live-code-preview-source">{value}</pre>
      )}
      <div ref={containerRef} className="live-code-preview-editor" />
      {unlocked || fill || isStatic ? (
        <button
          type="button"
          className="live-code-preview-copy"
          aria-label={copied ? "Copied" : "Copy code"}
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation();
            void copyText(value).then((ok) => {
              if (!ok) return;
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1600);
            });
          }}
        >
          {copied ? <Icons.Check /> : <Icons.Copy />}
        </button>
      ) : null}
      {lockScroll && !unlocked ? (
        <button
          type="button"
          className={`live-code-preview-lock${hint ? " live-code-preview-lock--hint" : ""}`}
          aria-label="Click to scroll this code"
          onWheel={() => setHint(true)}
          onClick={() => {
            setUnlocked(true);
            setHint(false);
            if (!plain) setActivated(true);
          }}
        >
          {hint ? <span className="live-code-preview-lock-hint">Click to scroll</span> : null}
        </button>
      ) : null}
    </div>
  );
}
