import { useEditorTabOverflowMode } from "../../hooks/useEditorTabOverflowMode";
import { useDockSidePanelMode } from "../../hooks/useDockSidePanelMode";
import type { DockPanelMode, DockSide } from "../../workspace/workspaceDockStorage";

const EDITOR_TAB_LABELS = ["verse.verse", "utils.verse", "game.verse", "ui.verse", "data.verse"];

// Representative panels for each rail's default home — the preview illustrates
// how the side stacks, not which panels must live there.
const SIDE_PANELS: Record<DockSide, [string, string]> = {
  left: ["Duckies", "Content"],
  right: ["Outline", "History"],
};

function RailPreview({ side, mode }: { side: DockSide; mode: DockPanelMode }) {
  const [first, second] = SIDE_PANELS[side];
  const label = side === "left" ? "Left panel" : "Right panel";

  return (
    <div className="appearance-layout-preview-block">
      <span className="appearance-layout-preview-caption">{label}</span>
      <div className={`appearance-layout-preview-frame appearance-layout-preview-sidebar appearance-layout-preview-sidebar--${mode}`}>
        {mode === "tabs" ? (
          <>
            <div className="appearance-layout-preview-panel-tabs">
              <span className="is-active">{first}</span>
              <span>{second}</span>
            </div>
            <div className="appearance-layout-preview-panel-body">
              <span className="appearance-layout-preview-line" />
              <span className="appearance-layout-preview-line short" />
              <span className="appearance-layout-preview-line" />
            </div>
          </>
        ) : (
          <div className="appearance-layout-preview-stack">
            <div className="appearance-layout-preview-stack-pane">
              <span className="appearance-layout-preview-stack-label">{first}</span>
              <span className="appearance-layout-preview-line" />
              <span className="appearance-layout-preview-line short" />
            </div>
            <div className="appearance-layout-preview-stack-divider" />
            <div className="appearance-layout-preview-stack-pane">
              <span className="appearance-layout-preview-stack-label">{second}</span>
              <span className="appearance-layout-preview-line" />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export function AppearanceLayoutPreview() {
  const { mode: editorMode } = useEditorTabOverflowMode();
  const { mode: leftMode } = useDockSidePanelMode("left");
  const { mode: rightMode } = useDockSidePanelMode("right");

  return (
    <div className="appearance-live-preview">
      <div className="appearance-live-preview-label">Live preview</div>

      <div className="appearance-layout-preview-block">
        <span className="appearance-layout-preview-caption">Editor tabs</span>
        <div className={`appearance-layout-preview-frame appearance-layout-preview-editor appearance-layout-preview-editor--${editorMode}`}>
          <div className="appearance-layout-preview-tabbar">
            {EDITOR_TAB_LABELS.map((label, index) => (
              <span
                key={label}
                className={`appearance-layout-preview-tab${index === 0 ? " is-active" : ""}`}
              >
                {label}
              </span>
            ))}
          </div>
          <div className="appearance-layout-preview-editor-body" />
        </div>
      </div>

      <RailPreview side="left" mode={leftMode} />
      <RailPreview side="right" mode={rightMode} />
    </div>
  );
}
