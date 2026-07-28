import { useEffect, useRef, useState } from "react";
import type { editor } from "monaco-editor";

import { useAppearance } from "../../theme/AppearanceContext";
import { MONACO_EMBEDDED_OVERFLOW_OPTIONS } from "../../verse-editor/monaco/embeddedEditorOverflow";
import { forceFullTokenization } from "../../verse-editor/monaco/forceFullTokenization";
import { setupMonaco } from "../../verse-editor/monaco/setupMonaco";
import { applyVerseMonacoTheme } from "../../verse-editor/monaco/verseTheme";
import {
  applyMonacoEditorFont,
  ensureMonacoFontReady,
  MONACO_EDITOR_FONT_SIZE,
} from "../../verse-editor/monaco/resolveMonacoFontFamily";
import { useMonacoEditorLayout } from "../../verse-editor/monaco/useMonacoEditorLayout";
import { VERSE_SYNTAX_PREVIEW_SAMPLE } from "./verseSyntaxPreviewSample";
import "../../verse-editor/verse-editor.css";

export function VerseSyntaxPreview() {
  const containerRef = useRef<HTMLDivElement>(null);
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<typeof import("monaco-editor") | null>(null);
  const { cssVars, foundation, appearanceReady } = useAppearance();
  const appearanceRef = useRef({ cssVars, foundation });
  appearanceRef.current = { cssVars, foundation };

  const [monacoReady, setMonacoReady] = useState(false);

  useMonacoEditorLayout(containerRef, editorRef, monacoReady);

  useEffect(() => {
    if (!appearanceReady) return;
    let cancelled = false;

    void (async () => {
      const container = containerRef.current;
      if (!container) return;

      const monaco = await setupMonaco();
      if (cancelled) return;

      monacoRef.current = monaco;
      applyVerseMonacoTheme(monaco, appearanceRef.current.cssVars, appearanceRef.current.foundation);

      const fontFamily = await ensureMonacoFontReady(appearanceRef.current.cssVars);
      if (cancelled) return;

      const ed = monaco.editor.create(container, {
        value: VERSE_SYNTAX_PREVIEW_SAMPLE,
        language: "verse",
        theme: "verse-dark",
        readOnly: true,
        domReadOnly: true,
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        lineNumbers: "on",
        lineNumbersMinChars: 3,
        folding: true,
        showFoldingControls: "mouseover",
        glyphMargin: true,
        fontSize: MONACO_EDITOR_FONT_SIZE,
        fontFamily,
        fontLigatures: false,
        fontWeight: "normal",
        mouseWheelZoom: false,
        padding: { top: 8, bottom: 8 },
        overviewRulerLanes: 0,
        hideCursorInOverviewRuler: true,
        scrollbar: { verticalScrollbarSize: 8, horizontalScrollbarSize: 8 },
        renderLineHighlight: "all",
        matchBrackets: "always",
        bracketPairColorization: { enabled: false },
        guides: { bracketPairs: false, indentation: true },

        ...MONACO_EMBEDDED_OVERFLOW_OPTIONS,
      });

      editorRef.current = ed;
      setMonacoReady(true);
      applyVerseMonacoTheme(monaco, appearanceRef.current.cssVars, appearanceRef.current.foundation, [ed]);

      // Colour the whole sample on open. Like the other editor hosts, this preview mounts with
      // automaticLayout off, so Monaco's initial tokenization pass sees a zero-height viewport and
      // renders everything as default tokens (mtk1) until a scroll/edit. Force it now.
      forceFullTokenization(monaco, ed);
    })();

    return () => {
      cancelled = true;
      setMonacoReady(false);
      editorRef.current?.dispose();
      editorRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount once appearance is ready
  }, [appearanceReady]);

  useEffect(() => {
    if (!monacoReady || !monacoRef.current || !appearanceReady) return;
    applyVerseMonacoTheme(monacoRef.current, cssVars, foundation, [editorRef.current]);
    void ensureMonacoFontReady(cssVars).then((fontFamily) => {
      if (editorRef.current) applyMonacoEditorFont(editorRef.current, fontFamily);
    });
  }, [cssVars, foundation, monacoReady, appearanceReady]);

  return (
    <div className="appearance-live-preview appearance-verse-preview-wrap">
      <div className="appearance-live-preview-label">Live preview</div>
      <div className="appearance-verse-preview">
        <div ref={containerRef} className="appearance-verse-preview-editor" />
      </div>
    </div>
  );
}
