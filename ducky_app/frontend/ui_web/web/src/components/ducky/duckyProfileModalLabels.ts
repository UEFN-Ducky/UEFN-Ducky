/** Editor footer — picker in-flight create must not become "Saving…". */
export function editorPrimaryLabel(opts: {
  saving: boolean;
  isCreate: boolean;
  hasUnsavedChanges: boolean;
}): string {
  if (opts.saving) return "Saving…";
  if (opts.isCreate) return "Create ducky";
  return opts.hasUnsavedChanges ? "Save ducky · pending" : "Save ducky";
}
