import { registryKey } from "../utils/isVerseFile";
import { useReplayState, type ReplayController } from "./ReplayController";

interface ReplayOverlayProps {
  /** The path this editor host renders — overlay only shows when it matches the replay. */
  path: string;
  controller: ReplayController;
}

function IconPrev() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
      <path d="M6 5h2v14H6zM20 5v14l-11-7z" />
    </svg>
  );
}

function IconNext() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
      <path d="M16 5h2v14h-2zM4 5l11 7-11 7z" />
    </svg>
  );
}

function IconPlay() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
      <path d="M7 5l12 7-12 7z" />
    </svg>
  );
}

function IconPause() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
      <path d="M7 5h4v14H7zM13 5h4v14h-4z" />
    </svg>
  );
}

function IconClose() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M18 6L6 18M6 6l12 12" />
    </svg>
  );
}

/**
 * Floating transport bar shown over the editor while an edit is being replayed.
 * Renders only for the file currently under replay. Lets the user step back/forward,
 * play/pause the walkthrough, and close it.
 */
export function ReplayOverlay({ path, controller }: ReplayOverlayProps) {
  const state = useReplayState(controller);

  // Match on the editor-registry key (Content/-prefixed, lowercased), NOT raw normPath:
  // the replay's path is the agent's relative_path (no Content/ prefix), while this host's
  // path carries it. Comparing normPath would never match and the transport would stay hidden.
  if (!state.active || !state.path || registryKey(state.path) !== registryKey(path)) return null;

  const hasSteps = state.total > 0;
  const atStart = state.index <= 0;
  const atEnd = state.index >= state.total - 1;
  const position = hasSteps ? state.index + 1 : 0;

  return (
    <div className="verse-replay-overlay no-drag" role="group" aria-label="Edit replay controls">
      <span className="verse-replay-overlay-label">Replaying edit</span>

      <div className="verse-replay-overlay-transport">
        <button
          type="button"
          className="verse-replay-btn"
          title="Previous change"
          aria-label="Previous change"
          disabled={!hasSteps || atStart}
          onClick={() => controller.prev()}
        >
          <IconPrev />
        </button>

        <button
          type="button"
          className="verse-replay-btn verse-replay-btn--primary"
          title={state.playing ? "Pause" : "Play"}
          aria-label={state.playing ? "Pause" : "Play"}
          disabled={!hasSteps}
          onClick={() => controller.toggle()}
        >
          {state.playing ? <IconPause /> : <IconPlay />}
        </button>

        <button
          type="button"
          className="verse-replay-btn"
          title="Next change"
          aria-label="Next change"
          disabled={!hasSteps || atEnd}
          onClick={() => controller.next()}
        >
          <IconNext />
        </button>
      </div>

      <span className="verse-replay-overlay-count">
        {hasSteps ? `${position} / ${state.total}` : "no changes"}
      </span>

      <button
        type="button"
        className="verse-replay-btn verse-replay-btn--close"
        title="Close replay"
        aria-label="Close replay"
        onClick={() => controller.stop()}
      >
        <IconClose />
      </button>
    </div>
  );
}
