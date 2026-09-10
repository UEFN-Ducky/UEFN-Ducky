import { LiveCodePreview } from "../../verse-editor/components/LiveCodePreview";
import { VERSE_SYNTAX_PREVIEW_SAMPLE } from "./verseSyntaxPreviewSample";

export function VerseSyntaxPreview() {
  return (
    <div className="appearance-live-preview appearance-verse-preview-wrap">
      <div className="appearance-live-preview-label">Live preview</div>
      <div className="appearance-verse-preview">
        <LiveCodePreview
          className="appearance-verse-preview-editor"
          value={VERSE_SYNTAX_PREVIEW_SAMPLE}
          language="verse"
          fill
        />
      </div>
    </div>
  );
}
