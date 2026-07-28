import { useEffect, useState } from "react";

import type { FileEditData } from "../../types/panel";
import { applyDecoration, clearAllDecorations } from "../monaco/decorations";
import { buildReplaySteps, type ReplayStep } from "../queue/buildReplayActions";
import type { EditorActionQueue } from "../queue/EditorActionQueue";
import { basename } from "../utils/isVerseFile";

export interface ReplayState {
  /** A replay session is running (overlay is shown for state.path). */
  active: boolean;
  /** File being replayed (raw path, as opened). */
  path: string | null;
  displayName: string;
  /** 0-based index of the current step. */
  index: number;
  total: number;
  playing: boolean;
}

const IDLE: ReplayState = {
  active: false,
  path: null,
  displayName: "",
  index: 0,
  total: 0,
  playing: false,
};

/** Dwell on each change while auto-playing before advancing to the next. */
const AUTO_ADVANCE_MS = 1200;

type OpenFn = (path: string, name: string, options?: { activate?: boolean }) => void;

/**
 * Drives a stepped "replay this edit" walkthrough on the CURRENT buffer.
 *
 * Why this exists instead of the fire-and-forget EditorActionQueue: the queue plays a
 * batch straight through with fixed timers, and its scroll/highlight steps silently
 * no-op if `getEditor(path)` returns undefined — which is exactly what happens the first
 * time a replay opens a not-yet-mounted file (Monaco registers asynchronously, after the
 * fixed open delay). So the file "opened but nothing played". This controller instead
 * waits for the editor to actually register (`waitForEditor`) before touching it, and
 * exposes play/pause/prev/next so the on-file overlay can drive it.
 */
export class ReplayController {
  private listeners = new Set<() => void>();
  private state: ReplayState = IDLE;
  private steps: ReplayStep[] = [];
  private decorationIds: string[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;
  /** Bumped on every start()/stop() so stale async waits/timers bail out. */
  private runToken = 0;

  constructor(
    private readonly queue: EditorActionQueue,
    private readonly openFile: OpenFn,
    private readonly requestSplit?: () => void,
  ) {}

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  };

  getState = (): ReplayState => this.state;

  private emit(patch?: Partial<ReplayState>) {
    this.state = patch ? { ...this.state, ...patch } : { ...this.state };
    for (const fn of this.listeners) fn();
  }

  private editor() {
    return this.state.path ? this.queue.getEditor(this.state.path) : undefined;
  }

  private clearDecorations() {
    const ed = this.editor();
    if (ed) clearAllDecorations(ed, this.decorationIds);
    this.decorationIds = [];
  }

  private async waitForEditor(path: string, token: number, timeoutMs = 5000) {
    const start = Date.now();
    let ed = this.queue.getEditor(path);
    while (!ed && this.runToken === token && Date.now() - start < timeoutMs) {
      await new Promise((r) => setTimeout(r, 60));
      ed = this.queue.getEditor(path);
    }
    return this.runToken === token ? ed : undefined;
  }

  /** Entry point for the replay button. */
  startFromEdit(edit: FileEditData): void {
    const { path, steps } = buildReplaySteps(edit);
    if (!path) return;
    void this.start(path, steps);
  }

  private async start(path: string, steps: ReplayStep[]): Promise<void> {
    this.stopTimer();
    this.clearDecorations();
    const token = ++this.runToken;
    this.steps = steps;
    this.emit({
      active: true,
      path,
      displayName: basename(path),
      index: 0,
      total: steps.length,
      playing: false,
    });

    // Open beside the chat and wait for Monaco to actually register before decorating.
    this.openFile(path, basename(path), { activate: true });
    this.requestSplit?.();

    const ed = await this.waitForEditor(path, token);
    if (this.runToken !== token) return; // superseded by a newer replay / stopped
    if (!ed || steps.length === 0) return; // overlay stays up so the user can close it

    this.renderStep(0);
    this.play();
  }

  private renderStep(i: number) {
    const ed = this.editor();
    const step = this.steps[i];
    if (!ed || !step) return;
    this.clearDecorations();
    ed.revealLineInCenter(step.scrollLine);
    this.decorationIds = applyDecoration(ed, step.style, step.range);
  }

  goTo(i: number): void {
    if (!this.state.active || this.steps.length === 0) return;
    const clamped = Math.max(0, Math.min(i, this.steps.length - 1));
    this.renderStep(clamped);
    this.emit({ index: clamped });
  }

  next(): void {
    if (this.state.index >= this.steps.length - 1) {
      this.pause();
      return;
    }
    this.goTo(this.state.index + 1);
  }

  prev(): void {
    this.pause();
    this.goTo(this.state.index - 1);
  }

  play(): void {
    if (!this.state.active || this.steps.length === 0) return;
    // Replaying from the end restarts from the top.
    if (this.state.index >= this.steps.length - 1) this.goTo(0);
    this.emit({ playing: true });
    this.scheduleAdvance();
  }

  pause(): void {
    this.stopTimer();
    if (this.state.playing) this.emit({ playing: false });
  }

  toggle(): void {
    if (this.state.playing) this.pause();
    else this.play();
  }

  private scheduleAdvance() {
    this.stopTimer();
    const token = this.runToken;
    this.timer = setTimeout(() => {
      if (this.runToken !== token || !this.state.playing) return;
      if (this.state.index >= this.steps.length - 1) {
        this.pause();
        return;
      }
      this.goTo(this.state.index + 1);
      this.scheduleAdvance();
    }, AUTO_ADVANCE_MS);
  }

  private stopTimer() {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  stop(): void {
    this.stopTimer();
    this.clearDecorations();
    this.runToken += 1;
    this.steps = [];
    this.emit({ ...IDLE });
  }
}

/** Subscribe a component to a ReplayController's state. */
export function useReplayState(controller: ReplayController | null): ReplayState {
  const [state, setState] = useState<ReplayState>(() => controller?.getState() ?? IDLE);
  useEffect(() => {
    if (!controller) return;
    setState(controller.getState());
    return controller.subscribe(() => setState(controller.getState()));
  }, [controller]);
  return state;
}
