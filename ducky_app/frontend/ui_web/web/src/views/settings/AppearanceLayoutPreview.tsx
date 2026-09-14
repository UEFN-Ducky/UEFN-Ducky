import { useEditorTabOverflowMode } from "../../hooks/useEditorTabOverflowMode";

const EDITOR_TAB_LABELS = ["verse.verse", "utils.verse", "game.verse", "ui.verse", "data.verse"];

export function AppearanceLayoutPreview() {
  const { mode: editorMode } = useEditorTabOverflowMode();

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
    </div>
  );
}
