import type { editor } from "monaco-editor";

/** Viewport-fixed suggest/hover/peek widgets — avoids clipping in overflow:hidden panel shells. */
export const MONACO_EMBEDDED_OVERFLOW_OPTIONS = {
  fixedOverflowWidgets: true,
} as const satisfies Pick<editor.IStandaloneEditorConstructionOptions, "fixedOverflowWidgets">;
