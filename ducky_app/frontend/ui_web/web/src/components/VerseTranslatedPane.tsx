import { useEffect, useRef, useState } from "react";
import type { editor } from "monaco-editor";

import { getApi } from "../hooks/usePanelApi";
import { setFileTranslateStatus } from "../navigation/fileTranslateStatus";
import {
  readTranslationModel,
  readTranslationUiLang,
} from "../navigation/openVerseTranslatedTab";
import {
  VERSE_TRANSLATE_CHUNK_CHARS,
  VERSE_TRANSLATE_SYSTEM,
  chunkNeedsLlm,
  looksLikeWeakTranslation,
  splitIntoLineChunks,
  stripTranslateFences,
  verseChunkCacheKey,
  verseTranslateCacheKey,
} from "../navigation/verseFileTranslate";
import { pluginLlmCompleteAsync, type PluginLlmAbort } from "../plugin-ui/pluginLlmAsync";
import { useAppearance } from "../theme/AppearanceContext";
import { readVerseFile } from "../verse-editor/api/verseEditorApi";
import { MONACO_EMBEDDED_OVERFLOW_OPTIONS } from "../verse-editor/monaco/embeddedEditorOverflow";
import {
  applyMonacoEditorFont,
  ensureMonacoFontReady,
  MONACO_EDITOR_FONT_SIZE,
} from "../verse-editor/monaco/resolveMonacoFontFamily";
import { setupMonaco } from "../verse-editor/monaco/setupMonaco";
import { useMonacoEditorLayout } from "../verse-editor/monaco/useMonacoEditorLayout";
import { applyVerseMonacoTheme } from "../verse-editor/monaco/verseTheme";
import { basename, monacoLanguageForPath } from "../verse-editor/utils/isVerseFile";
import "../verse-editor/verse-editor.css";

const PLUGIN_ID = "translation";
const MAX_CHARS = 72_000;
/** Per-chunk timeout — whole-file one-shots starved Ollama at ~5k chars. */
const LLM_CHUNK_TIMEOUT_MS = 90_000;

async function sha256Hex(text: string): Promise<string> {
  const data = new TextEncoder().encode(text);
  const buf = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

type Phase = "idle" | "reading" | "cache" | "llm" | "done" | "error" | "stopped";

interface VerseTranslatedPaneProps {
  relativePath: string;
}

export function VerseTranslatedPane({ relativePath }: VerseTranslatedPaneProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<typeof import("monaco-editor") | null>(null);
  const abortRef = useRef<PluginLlmAbort>({ aborted: false });
  const { cssVars, foundation, appearanceReady } = useAppearance();
  const appearanceRef = useRef({ cssVars, foundation });
  appearanceRef.current = { cssVars, foundation };

  const [monacoReady, setMonacoReady] = useState(false);
  const [status, setStatus] = useState("Loading…");
  const [error, setError] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [elapsedSec, setElapsedSec] = useState(0);
  const [charsHint, setCharsHint] = useState("");
  const lang = readTranslationUiLang();
  const busy = phase === "reading" || phase === "cache" || phase === "llm";

  useMonacoEditorLayout(containerRef, editorRef, monacoReady);

  useEffect(() => {
    if (!busy) return;
    const t0 = Date.now();
    setElapsedSec(0);
    const id = window.setInterval(() => {
      setElapsedSec(Math.floor((Date.now() - t0) / 1000));
    }, 250);
    return () => window.clearInterval(id);
  }, [busy, relativePath, lang]);

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
        value: "",
        language: monacoLanguageForPath(relativePath),
        theme: "verse-dark",
        readOnly: true,
        domReadOnly: true,
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        lineNumbers: "on",
        lineNumbersMinChars: 3,
        folding: true,
        showFoldingControls: "mouseover",
        glyphMargin: false,
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
      applyMonacoEditorFont(ed, fontFamily);
      applyVerseMonacoTheme(monaco, appearanceRef.current.cssVars, appearanceRef.current.foundation, [ed]);
    })();

    return () => {
      cancelled = true;
      editorRef.current?.dispose();
      editorRef.current = null;
      setMonacoReady(false);
    };
  }, [appearanceReady, relativePath]);

  useEffect(() => {
    if (!monacoReady || !editorRef.current) return;
    let cancelled = false;
    abortRef.current = { aborted: false };

    const setEditorText = (text: string) => {
      const ed = editorRef.current;
      const model = ed?.getModel();
      const monaco = monacoRef.current;
      if (ed && model && monaco) {
        monaco.editor.setModelLanguage(model, monacoLanguageForPath(relativePath));
        model.setValue(text);
      } else if (ed) ed.setValue(text);
    };

    void (async () => {
      setError("");
      setCharsHint("");
      setPhase("reading");
      setStatus("Reading file…");
      setFileTranslateStatus(relativePath, lang, "translating", "Translating…");
      try {
        const { content } = await readVerseFile(relativePath);
        if (cancelled || abortRef.current.aborted) {
          setPhase("stopped");
          setStatus("Stopped");
          setFileTranslateStatus(relativePath, lang, "idle");
          return;
        }
        const source = content.length > MAX_CHARS ? content.slice(0, MAX_CHARS) : content;
        setCharsHint(
          content.length > MAX_CHARS
            ? `${MAX_CHARS.toLocaleString()} / ${content.length.toLocaleString()} chars`
            : `${content.length.toLocaleString()} chars`,
        );
        setEditorText(source);

        // v2 salt invalidates lazy one-shot caches that only swapped a few keywords.
        const digest = await sha256Hex(`fullv2\0${lang}\0${relativePath}\0${source}`);
        const fileKey = verseTranslateCacheKey(lang, digest);
        const api = getApi();

        let translated = "";
        let fromCache = false;
        if (api?.plugin_cache_get) {
          setPhase("cache");
          setStatus("Checking cache…");
          setFileTranslateStatus(relativePath, lang, "translating", "Checking cache…");
          const cached = await api.plugin_cache_get(PLUGIN_ID, fileKey);
          if (cancelled || abortRef.current.aborted) {
            setPhase("stopped");
            setStatus("Stopped");
            setFileTranslateStatus(relativePath, lang, "idle");
            return;
          }
          const data = cached?.ok && cached.data && typeof cached.data === "object" ? cached.data : null;
          const text = data && typeof data.text === "string" ? data.text : "";
          if (text.trim() && !looksLikeWeakTranslation(source, text)) {
            translated = text;
            fromCache = true;
          }
        }

        if (!translated) {
          setPhase("llm");
          const chunks = splitIntoLineChunks(source, VERSE_TRANSLATE_CHUNK_CHARS);
          const outParts: string[] = [];
          const model = readTranslationModel();
          let llmCalls = 0;
          let chunkHits = 0;

          for (let i = 0; i < chunks.length; i++) {
            if (cancelled || abortRef.current.aborted) {
              setPhase("stopped");
              setStatus("Stopped");
              setFileTranslateStatus(relativePath, lang, "idle");
              return;
            }
            const chunk = chunks[i];

            // Blank / path-only — no tokens.
            if (!chunkNeedsLlm(chunk)) {
              outParts.push(chunk);
              setEditorText(outParts.join("") + chunks.slice(i + 1).join(""));
              continue;
            }

            // Content-addressed chunk cache — edit one line, reuse the rest.
            let part = "";
            const chunkDigest = await sha256Hex(`vc1\0${lang}\0${chunk}`);
            const chunkKey = verseChunkCacheKey(lang, chunkDigest);
            if (api?.plugin_cache_get) {
              const hit = await api.plugin_cache_get(PLUGIN_ID, chunkKey);
              const data = hit?.ok && hit.data && typeof hit.data === "object" ? hit.data : null;
              const text = data && typeof data.text === "string" ? data.text : "";
              if (text.trim() && !looksLikeWeakTranslation(chunk, text)) {
                part = text;
                chunkHits += 1;
              }
            }

            if (!part) {
              const label = `Translating ${lang} · chunk ${i + 1}/${chunks.length}`;
              setStatus(
                chunkHits
                  ? `${label} (${chunkHits} cached)`
                  : label,
              );
              setFileTranslateStatus(relativePath, lang, "translating", label);
              // Minimal user prompt — system already has the rules.
              const user = `Target language: ${lang}\nChunk ${i + 1}/${chunks.length}:\n\n${chunk}`;
              const res = await pluginLlmCompleteAsync(
                PLUGIN_ID,
                VERSE_TRANSLATE_SYSTEM,
                user,
                model,
                LLM_CHUNK_TIMEOUT_MS,
                abortRef.current,
              );
              if (cancelled || abortRef.current.aborted || res.cancelled) {
                setPhase("stopped");
                setStatus("Stopped");
                setFileTranslateStatus(relativePath, lang, "idle");
                return;
              }
              if (!res?.ok) throw new Error(res?.error || "Translation failed");
              part = stripTranslateFences(String(res.text || ""));
              if (!part.trim()) {
                throw new Error(`Empty translation for chunk ${i + 1}/${chunks.length}`);
              }
              if (looksLikeWeakTranslation(chunk, part)) {
                throw new Error(
                  `Chunk ${i + 1}/${chunks.length} barely translated — pick a stronger model ` +
                    `(OpenAI / Anthropic / Gemini) in Settings → Languages, or retry.`,
                );
              }
              llmCalls += 1;
              // Persist good chunks immediately — mid-fail must not burn them again.
              if (api?.plugin_cache_set) {
                await api.plugin_cache_set(PLUGIN_ID, chunkKey, {
                  text: part,
                  lang,
                  mode: "chunkv1",
                });
              }
            } else {
              setStatus(`Assembling ${lang} · chunk ${i + 1}/${chunks.length} (cached)`);
            }

            outParts.push(part);
            setEditorText(outParts.join("") + chunks.slice(i + 1).join(""));
          }

          translated = outParts.join("");
          if (looksLikeWeakTranslation(source, translated)) {
            throw new Error(
              "Model returned mostly English — pick an API model (OpenAI / Anthropic / Gemini) in Settings → Languages.",
            );
          }
          if (api?.plugin_cache_set) {
            await api.plugin_cache_set(PLUGIN_ID, fileKey, {
              text: translated,
              lang,
              path: relativePath,
              hash: digest,
              mode: "fullv2",
              chunks: chunks.length,
              llmCalls,
              chunkHits,
            });
          }
          fromCache = llmCalls === 0 && chunkHits > 0;
        }

        if (cancelled || abortRef.current.aborted) {
          setPhase("stopped");
          setStatus("Stopped");
          setFileTranslateStatus(relativePath, lang, "idle");
          return;
        }
        setEditorText(translated);
        setPhase("done");
        setStatus(
          fromCache
            ? `Visual only · ${lang} · cached`
            : content.length > MAX_CHARS
              ? `Visual only · first ${MAX_CHARS.toLocaleString()} chars · ${lang}`
              : `Visual only · ${lang} · full file`,
        );
        setFileTranslateStatus(
          relativePath,
          lang,
          "cached",
          fromCache ? "Cached" : "Translated",
        );
      } catch (e) {
        if (cancelled || abortRef.current.aborted) {
          setPhase("stopped");
          setStatus("Stopped");
          setFileTranslateStatus(relativePath, lang, "idle");
          return;
        }
        const msg = e instanceof Error ? e.message : String(e);
        setError(msg);
        setPhase("error");
        setStatus("Failed");
        setFileTranslateStatus(relativePath, lang, "error", "Failed");
      }
    })();

    return () => {
      cancelled = true;
      abortRef.current.aborted = true;
    };
  }, [monacoReady, relativePath, lang]);

  const stopTranslate = () => {
    abortRef.current.aborted = true;
    setPhase("stopped");
    setStatus("Stopping…");
    setFileTranslateStatus(relativePath, lang, "idle");
  };

  const phasePct =
    phase === "done"
      ? 100
      : phase === "llm"
        ? 70
        : phase === "cache"
          ? 40
          : phase === "reading"
            ? 15
            : phase === "error" || phase === "stopped"
              ? 100
              : 0;

  return (
    <div className="verse-translated-pane" data-no-translate>
      <div className="verse-translated-banner">
        <strong>{basename(relativePath)}</strong>
        <span>
          Visual translation ({lang}). Entire file — all words. Edit the English source to change
          logic; this view is read-only and cached.
        </span>
        <span className="verse-translated-status">
          {error || status}
          {busy && elapsedSec > 0 ? ` · ${elapsedSec}s` : ""}
          {charsHint ? ` · ${charsHint}` : ""}
        </span>
        {busy ? (
          <button
            type="button"
            className="verse-translated-stop-btn"
            onClick={stopTranslate}
            title="Stop this file translation"
          >
            Stop
          </button>
        ) : null}
      </div>
      {busy || phase === "error" || phase === "stopped" ? (
        <div
          className={`verse-translated-progress${phase === "error" ? " is-error" : ""}${
            phase === "stopped" ? " is-stopped" : ""
          }${phase === "llm" ? " is-indeterminate" : ""}`}
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={phase === "llm" ? undefined : phasePct}
          aria-label="File translation progress"
        >
          <div className="verse-translated-progress-fill" style={{ width: `${phasePct}%` }} />
        </div>
      ) : null}
      <div
        ref={containerRef}
        className={`verse-translated-editor verse-editor-container${monacoReady ? " is-visible" : ""}`}
      />
    </div>
  );
}
