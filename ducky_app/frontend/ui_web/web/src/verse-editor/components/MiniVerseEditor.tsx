import { useEffect, useRef, useState } from "react";

import type { editor } from "monaco-editor";

import { useAppearance } from "../../theme/AppearanceContext";
import { MONACO_EMBEDDED_OVERFLOW_OPTIONS } from "../monaco/embeddedEditorOverflow";
import { forceFullTokenization } from "../monaco/forceFullTokenization";
import {
  applyMonacoEditorFont,
  ensureMonacoFontReady,
} from "../monaco/resolveMonacoFontFamily";
import { setupMonaco } from "../monaco/setupMonaco";
import { useMonacoEditorLayout } from "../monaco/useMonacoEditorLayout";
import { applyVerseMonacoTheme } from "../monaco/verseTheme";

interface MiniVerseEditorProps {
  value: string;
  onChange: (value: string) => void;
}

/**
 * Compact Monaco Verse editor for the template creator — same language/theme stack
 * as the main editor, stripped down (no minimap, no wheel zoom).
 */
export function MiniVerseEditor({ value, onChange }: MiniVerseEditorProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<typeof import("monaco-editor") | null>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const suppressSyncRef = useRef(false);
  const { cssVars, foundation, appearanceReady } = useAppearance();
  const appearanceRef = useRef({ cssVars, foundation });
  appearanceRef.current = { cssVars, foundation };
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!appearanceReady) return;
    let cancelled = false;

    void (async () => {
      for (let i = 0; i < 60 && !containerRef.current; i += 1) {
        await new Promise((r) => requestAnimationFrame(r));
      }
      const container = containerRef.current;
      if (!container || cancelled) return;

      try {
        const monacoApi = await setupMonaco();
        if (cancelled) return;
        monacoRef.current = monacoApi;

        applyVerseMonacoTheme(
          monacoApi,
          appearanceRef.current.cssVars,
          appearanceRef.current.foundation,
        );

        const fontFamily = await ensureMonacoFontReady(appearanceRef.current.cssVars, 13);
        if (cancelled) return;

        const uri = monacoApi.Uri.parse("inmemory://vtm-template/scratch.verse");
        const existing = monacoApi.editor.getModel(uri);
        if (existing) {
          try {
            existing.dispose();
          } catch {
            /* already torn down */
          }
        }

        const model = monacoApi.editor.createModel(value, "verse", uri);
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
          fontSize: 13,
          lineHeight: 21,
          mouseWheelZoom: false,
          fontFamily,
          fontLigatures: false,
          tabSize: 2,
          insertSpaces: true,
          lineNumbers: "on",
          glyphMargin: false,
          folding: false,
          lineDecorationsWidth: 8,
          lineNumbersMinChars: 3,
          minimap: { enabled: false },
          wordWrap: "off",
          scrollBeyondLastLine: false,
          renderLineHighlight: "line",
          overviewRulerLanes: 0,
          hideCursorInOverviewRuler: true,
          overviewRulerBorder: false,
          scrollbar: {
            verticalScrollbarSize: 6,
            horizontalScrollbarSize: 6,
          },
          padding: { top: 8, bottom: 8 },
          contextmenu: true,
          quickSuggestions: true,
          suggestOnTriggerCharacters: true,
          ...MONACO_EMBEDDED_OVERFLOW_OPTIONS,
        });

        editorRef.current = ed;
        ed.onDidChangeModelContent(() => {
          if (suppressSyncRef.current) return;
          onChangeRef.current(ed.getValue());
        });

        if (!cancelled) {
          setReady(true);
          forceFullTokenization(monacoApi, ed);
        }
      } catch {
        if (!cancelled) setReady(false);
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
    // Mount once per open; value sync handled separately.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appearanceReady]);

  useEffect(() => {
    const ed = editorRef.current;
    if (!ed || !ready) return;
    if (ed.getValue() === value) return;
    suppressSyncRef.current = true;
    ed.setValue(value);
    suppressSyncRef.current = false;
  }, [value, ready]);

  useEffect(() => {
    const monacoApi = monacoRef.current;
    const ed = editorRef.current;
    if (!monacoApi || !ed || !ready) return;
    applyVerseMonacoTheme(monacoApi, cssVars, foundation, [ed]);
    void ensureMonacoFontReady(cssVars, 13).then((fontFamily) => {
      applyMonacoEditorFont(ed, fontFamily, 13);
    });
  }, [cssVars, foundation, ready]);

  useMonacoEditorLayout(containerRef, editorRef, ready);

  return <div ref={containerRef} className="vtm-mini-editor" />;
}
