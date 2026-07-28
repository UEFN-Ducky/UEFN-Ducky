import type { editor } from "monaco-editor";

/**
 * Force Monaco to syntax-colour the WHOLE file the moment it opens.
 *
 * Root cause of "everything is `mtk1` until I scroll/edit": the editor is created with
 * `automaticLayout: false`, so at construction it has a zero-height viewport and Monaco's
 * initial tokenization pass (`refreshAllVisibleLineTokens`) sees 0 visible lines. Both the
 * whole-doc `forceTokenization` and the on-scroll `refreshRange` bail out early while the
 * model's internal `_tokenizer` is unset (`if (!this._tokenizer) return`), and that tokenizer
 * only gets (re)built by a `resetTokenization` — which, on the mount-time 0-height viewport,
 * colours nothing. A scroll/edit later rebuilds+runs it, which is why interaction "fixes" it.
 *
 * Fix: on open, deterministically rebuild the tokenizer bound to the Verse TextMate support
 * via a language round-trip (`plaintext` → back), which is guaranteed NOT to early-return
 * (Monaco's `setLanguageId` only skips when the id is unchanged) and forces a full
 * `resetTokenization`. Both hops are synchronous in one JS tick, so the browser never paints
 * the intermediate plaintext state (no flicker). Then tokenize every line and repaint.
 *
 * Internal API (`model.tokenization.forceTokenization`) isn't in the public `ITextModel`
 * typings, so it's accessed defensively — a Monaco upgrade that renames it degrades to lazy
 * scroll/edit tokenization, never throws.
 */
type TokenizableModel = editor.ITextModel & {
  tokenization?: {
    forceTokenization?: (lineNumber: number) => void;
  };
};

// Cap synchronous tokenization so a very large file can't block the main thread on open;
// Monaco's on-demand viewport tokenization covers anything past the cap.
const MAX_SYNC_TOKENIZE_LINES = 6000;

export function forceFullTokenization(
  monaco: typeof import("monaco-editor"),
  ed: editor.IStandaloneCodeEditor,
): void {
  // Defer one frame so the editor's first layout has settled before we measure/tokenize.
  requestAnimationFrame(() => {
    const model = ed.getModel();
    if (!model || model.isDisposed()) return;
    try {
      // Give the view real dimensions first (automaticLayout is off).
      ed.layout();

      // Rebuild the tokenizer against the registered TextMate support. The round-trip is
      // synchronous — no intermediate plaintext paint — and forces resetTokenization even
      // though the id ends up unchanged (a same-id setModelLanguage would no-op).
      const lang = model.getLanguageId();
      if (lang && lang !== "plaintext") {
        monaco.editor.setModelLanguage(model, "plaintext");
        monaco.editor.setModelLanguage(model, lang);
      }

      // Now tokenize the whole document (the tokenizer exists after the round-trip) and
      // force a repaint so the freshly-computed tokens actually render.
      const lineCount = Math.min(model.getLineCount(), MAX_SYNC_TOKENIZE_LINES);
      (model as TokenizableModel).tokenization?.forceTokenization?.(lineCount);
      ed.render(true);
    } catch {
      /* internal API drifted across a Monaco upgrade — lazy tokenization still colours on scroll/edit */
    }
  });
}
