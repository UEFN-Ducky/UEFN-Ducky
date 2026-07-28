import type { editor } from "monaco-editor";

import type { EditorAction } from "./types";

import { applyDecoration, clearAllDecorations } from "../monaco/decorations";

import { basename, normPath, registryKey } from "../utils/isVerseFile";

import { followCodeSpeedMultiplier, getFollowCodeSettings } from "./followCodeSettings";



export type OpenFileOptions = { activate?: boolean };



export type EditorHandle = {

  path: string;

  editor: editor.IStandaloneCodeEditor;

};



export type QueueCallbacks = {

  onOpenFile: (path: string, name: string, options?: OpenFileOptions) => void;

  onRequestSplit?: () => void;

  onPlaybackChange?: (locked: boolean) => void;

  onFileSync?: (path: string) => void;

  onAgentWriteComplete?: (path: string) => void;

  onDirty?: (path: string, dirty: boolean) => void;

  /** Agent disk write is authoritative — update editor saved baseline. */
  onMarkSaved?: (path: string, content: string) => void;

  isPathDirty?: (path: string) => boolean;

};



function sleep(ms: number): Promise<void> {

  return new Promise((r) => setTimeout(r, ms));

}



export class EditorActionQueue {

  private queue: EditorAction[][] = [];

  private playing = false;

  private activePathCount = 0;

  private agentActivePaths = new Set<string>();

  private editors = new Map<string, editor.IStandaloneCodeEditor>();

  private callbacks: QueueCallbacks;



  constructor(callbacks: QueueCallbacks) {

    this.callbacks = callbacks;

  }



  isAgentActive(path: string): boolean {

    return this.agentActivePaths.has(registryKey(path));

  }



  registerEditor(path: string, ed: editor.IStandaloneCodeEditor): void {

    // registryKey (lowercased, Content/-prefixed): editors must be findable no matter
    // which casing the caller has — tree tabs use on-disk case, the Problems panel and
    // diagnostics registry use lowercase keys. Windows paths are case-insensitive.
    this.editors.set(registryKey(path), ed);

  }



  getEditor(path: string): editor.IStandaloneCodeEditor | undefined {

    return this.editors.get(registryKey(path));

  }



  /**
   * Poll until Monaco has registered an editor for `path` (or we give up).
   *
   * A freshly opened file mounts its editor asynchronously — well after the fixed
   * open-file delay — so the scroll_to/highlight actions that follow open_file in the
   * same batch would getEditor()===undefined and silently no-op ("agent opened the file
   * but never walked through it"). Awaiting registration here closes that cold-open race.
   */
  private async waitForEditor(

    path: string,

    timeoutMs = 4000,

  ): Promise<editor.IStandaloneCodeEditor | undefined> {

    const start = Date.now();

    let ed = this.getEditor(path);

    while (!ed && Date.now() - start < timeoutMs) {

      await sleep(60);

      ed = this.getEditor(path);

    }

    return ed;

  }



  revealIfOpen(path: string, line: number, column: number): boolean {

    const ed = this.getEditor(path);

    if (!ed) return false;

    ed.revealPositionInCenter({ lineNumber: line, column });

    ed.setPosition({ lineNumber: line, column });

    ed.focus();

    return true;

  }



  unregisterEditor(path: string): void {

    this.editors.delete(registryKey(path));

  }



  enqueue(batch: EditorAction[]): void {

    if (!batch.length) return;

    this.queue.push(batch);

    void this.pump();

  }



  private async pump(): Promise<void> {

    if (this.playing) return;

    this.playing = true;

    this.callbacks.onPlaybackChange?.(true);

    try {

      while (this.queue.length > 0) {

        const batch = this.queue.shift()!;

        const byPath = new Map<string, EditorAction[]>();

        for (const action of batch) {

          const path = normPath(action.path);

          const list = byPath.get(path);

          if (list) list.push(action);

          else byPath.set(path, [action]);

        }

        await Promise.all([...byPath.entries()].map(([path, actions]) => this.runPathActions(path, actions)));

      }

    } finally {

      this.playing = false;

      this.callbacks.onPlaybackChange?.(false);

    }

  }



  private async runPathActions(path: string, actions: EditorAction[]): Promise<void> {

    const key = registryKey(path);

    const isWrite = actions.some((action) => action.type === "apply_content");

    const ed = this.getEditor(path);

    // Writes used to silent-sync when no editor was mounted. Follow Code now
    // prepends open_file so cold opens can walk the diff — only bail when we
    // will not open (follow-code off / no open_file / not a forced replay).
    if (isWrite && !ed) {
      const settings = getFollowCodeSettings();
      const hasOpen = actions.some((action) => action.type === "open_file");
      const forced = actions.some((action) => action.force === true);
      const willOpen = hasOpen && (settings.enabled || forced);
      if (!willOpen) {
        this.callbacks.onFileSync?.(path);
        this.callbacks.onAgentWriteComplete?.(path);
        return;
      }
    }

    this.agentActivePaths.add(key);

    this.activePathCount += 1;

    const decorationIds: string[] = [];

    try {

      for (const action of actions) {

        await this.runAction(path, action, decorationIds);

      }

      if (actions.some((action) => action.type === "apply_content")) {
        this.callbacks.onAgentWriteComplete?.(path);
      }

    } finally {

      this.agentActivePaths.delete(key);

      this.activePathCount -= 1;

    }

  }



  private async runAction(

    path: string,

    action: EditorAction,

    decorationIds: string[],

  ): Promise<void> {

    // Read per action so toggling follow-code mid-playback takes effect immediately.
    const settings = getFollowCodeSettings();

    // force = explicit user replay: play even when follow-code is off or instant.
    const enabled = settings.enabled || action.force === true;

    let multiplier = enabled ? followCodeSpeedMultiplier(settings.speed) : 0;

    if (action.force && multiplier <= 0) multiplier = 1;

    const delay = (action.durationMs ?? 350) * multiplier;

    const wait = () => (delay > 0 ? sleep(delay) : Promise.resolve());



    switch (action.type) {

      case "open_file": {

        // Follow-code off: never open tabs for the agent — content still syncs below.
        if (!enabled) break;

        this.callbacks.onOpenFile(path, basename(path), {

          activate: action.activate ?? false,

        });

        this.callbacks.onRequestSplit?.();

        // Don't let the following scroll/highlight steps fire before Monaco mounts —
        // wait for the editor to register (bounded), then still honour the open delay.
        await this.waitForEditor(path);

        await wait();

        break;

      }

      case "scroll_to": {

        if (!enabled) break;

        const ed = this.getEditor(path);

        if (ed && action.range) {

          ed.revealLineInCenter(action.range.startLine);

        }

        await wait();

        break;

      }

      case "highlight":

      case "delete_preview":

      case "insert_preview": {

        // Instant speed: decorations would flash for 0ms — skip the work entirely.
        if (!enabled || multiplier <= 0) break;

        const ed = this.getEditor(path);

        if (ed && action.range && action.style) {

          clearAllDecorations(ed, decorationIds);

          decorationIds.length = 0;

          decorationIds.push(...applyDecoration(ed, action.style, action.range));

        }

        await wait();

        break;

      }

      case "apply_content": {

        // Agent workspace_write already hit disk — always apply into the open
        // buffer (even if the tab was dirty) and treat it as saved so Problems
        // / the checkmark refresh against the new content without a manual Save.
        const ed = this.getEditor(path);

        if (ed && action.text !== undefined) {

          const model = ed.getModel();

          if (model) {

            if (model.getValue() !== action.text) {

              ed.executeEdits("agent", [{ range: model.getFullModelRange(), text: action.text }]);

            }

            this.callbacks.onMarkSaved?.(path, action.text);

            this.callbacks.onDirty?.(path, false);

          }

        } else if (action.text !== undefined) {

          this.callbacks.onMarkSaved?.(path, action.text);

          this.callbacks.onDirty?.(path, false);

        }

        this.callbacks.onFileSync?.(path);

        await sleep(100);

        break;

      }

      case "clear_decorations": {

        const ed = this.getEditor(path);

        if (ed) clearAllDecorations(ed, decorationIds);

        decorationIds.length = 0;

        break;

      }

      case "set_vim_enabled":
      case "run_vim_command": {
        // Forward to Store plugins (e.g. vim) — core has no vim handlers.
        window.dispatchEvent(new CustomEvent("ducky-editor-plugin-action", { detail: action }));
        break;
      }

      default:

        break;

    }

  }

}


