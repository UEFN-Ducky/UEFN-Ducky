import type { editor } from "monaco-editor";
import { normPath } from "./utils/isVerseFile";

interface FileEditorSession {
  model: editor.ITextModel;
  savedContent: string;
  viewState: editor.ICodeEditorViewState | null;
  saving?: Promise<boolean>;
}

/** Models belong to open file tabs, not to the short-lived visible editor widget. */
export class FileEditorSessions {
  private sessions = new Map<string, FileEditorSession>();

  get(path: string): FileEditorSession | undefined {
    const session = this.sessions.get(normPath(path));
    return session && !session.model.isDisposed() ? session : undefined;
  }

  acquire(path: string, diskContent: string, create: () => editor.ITextModel): FileEditorSession {
    const existing = this.get(path);
    if (existing) {
      // Refresh clean buffers changed on disk while hidden; never replace an unsaved buffer.
      if (existing.model.getValue() === existing.savedContent) {
        if (existing.savedContent !== diskContent) existing.model.setValue(diskContent);
        existing.savedContent = diskContent;
      }
      return existing;
    }
    const session = { model: create(), savedContent: diskContent, viewState: null };
    this.sessions.set(normPath(path), session);
    return session;
  }

  savedContent(path: string): string {
    return this.get(path)?.savedContent ?? "";
  }

  markSaved(path: string, content: string): void {
    const session = this.get(path);
    if (session) session.savedContent = content;
  }

  /** Disk (agent write / external tool) is the new truth for a model no editor shows.
   * Applied as an edit so the previous buffer stays one undo away. */
  applyDiskContent(path: string, content: string): boolean {
    const session = this.get(path);
    if (!session) return false;
    const { model } = session;
    if (model.getValue() !== content) {
      model.pushEditOperations([], [{ range: model.getFullModelRange(), text: content }], () => null);
    }
    session.savedContent = content;
    return true;
  }

  isDirty(path: string): boolean {
    const session = this.get(path);
    return !!session && session.model.getValue() !== session.savedContent;
  }

  save(path: string, write: (path: string, content: string) => Promise<unknown>): Promise<boolean> {
    const session = this.get(path);
    if (!session) return Promise.resolve(false);
    if (session.saving) return session.saving;
    const content = session.model.getValue();
    if (content === session.savedContent) return Promise.resolve(true);
    session.saving = Promise.resolve().then(() => write(path, content)).then(() => {
      session.savedContent = content;
      // Typing during a save must keep the dirty dot and prevent Save & Close losing new edits.
      return !session.model.isDisposed() && session.model.getValue() === content;
    }).finally(() => { session.saving = undefined; });
    return session.saving;
  }

  retain(paths: Iterable<string>): void {
    const keep = new Set(Array.from(paths, normPath));
    for (const [path, session] of this.sessions) {
      if (keep.has(path)) continue;
      session.model.dispose();
      this.sessions.delete(path);
    }
  }

  dispose(): void {
    this.retain([]);
  }
}
