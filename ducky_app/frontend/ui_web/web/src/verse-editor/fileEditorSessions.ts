import type { editor } from "monaco-editor";
import { normPath } from "./utils/isVerseFile";

export interface DiskStat {
  exists: boolean;
  mtime_ns: number;
  size: number;
}

export const diskFingerprint = (stat: DiskStat): string => `${stat.exists}:${stat.mtime_ns}:${stat.size}`;

interface FileEditorSession {
  model: editor.ITextModel;
  savedContent: string;
  viewState: editor.ICodeEditorViewState | null;
  saving?: Promise<boolean>;
  /** One trailing save shared by every Ctrl+S that lands while a write is in flight. */
  queued?: Promise<boolean>;
}

/** Models belong to open file tabs, not to the short-lived visible editor widget. */
export class FileEditorSessions {
  private sessions = new Map<string, FileEditorSession>();
  /** Disk fingerprint + canonical path of each file's last full read. */
  private disk = new Map<string, { fp: string; path: string }>();

  /** Disk text for a (re)mount. A tab switch back to a file unchanged on disk costs one
   * stat instead of shipping the whole file over the bridge again. */
  async readForMount(
    path: string,
    read: (path: string) => Promise<{ content: string; path: string }>,
    stat?: (path: string) => Promise<DiskStat>,
  ): Promise<{ content: string; path: string }> {
    const key = normPath(path);
    // Stat before the read: a change landing in between yields a newer fingerprint next time.
    const fp = stat ? await stat(path).then(diskFingerprint, () => "") : "";
    const known = this.disk.get(key);
    const session = this.get(path);
    if (fp && known?.fp === fp && session) return { content: session.savedContent, path: known.path };
    const result = await read(path);
    if (fp) this.disk.set(key, { fp, path: result.path });
    else this.disk.delete(key);
    return result;
  }

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

  /** The watcher's stat is the disk echo of our own last save (or last full read). */
  isDiskEcho(path: string, stat: DiskStat): boolean {
    return this.disk.get(normPath(path))?.fp === diskFingerprint(stat);
  }

  private noteSavedStat(path: string, result: unknown): void {
    const r = result as { mtime_ns?: unknown; size?: unknown } | null | undefined;
    if (typeof r?.mtime_ns !== "number" || typeof r.size !== "number") return;
    const key = normPath(path);
    const fp = diskFingerprint({ exists: true, mtime_ns: r.mtime_ns, size: r.size });
    this.disk.set(key, { fp, path: this.disk.get(key)?.path ?? path });
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
    if (session.saving) {
      // Save spam: one write in flight plus one trailing write of the latest buffer, however
      // many presses land meanwhile, so the final content always reaches disk.
      session.queued ??= session.saving.catch(() => false).then(() => {
        session.queued = undefined;
        return this.save(path, write);
      });
      return session.queued;
    }
    const content = session.model.getValue();
    if (content === session.savedContent) return Promise.resolve(true);
    session.saving = Promise.resolve().then(() => write(path, content)).then((result) => {
      session.savedContent = content;
      this.noteSavedStat(path, result);
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
      this.disk.delete(path);
    }
  }

  dispose(): void {
    this.retain([]);
  }
}
