import type { DuckyOSStorePatchNote } from "../../../types/panel";
import { formatPatchDate } from "./storeData";

type Props = {
  notes: DuckyOSStorePatchNote[] | null;
};

export function PatchNotesList({ notes }: Props) {
  if (notes === null) {
    return <p className="ds-panel-desc ds-panel-desc--mute">Loading…</p>;
  }
  if (notes.length === 0) {
    return <p className="ds-panel-desc ds-panel-desc--mute">No patch notes yet.</p>;
  }
  return (
    <ol className="ds-changelog">
      {notes.map((v) => {
        const date = formatPatchDate(v.created_at);
        return (
          <li key={`${v.version || ""}-${v.created_at || ""}-${v.changelog || ""}`}>
            <span className="ds-changelog-ver">v{v.version || ""}</span>
            {date ? <span className="ds-changelog-date">{date}</span> : null}
            <p className="ds-changelog-note">{v.changelog || "—"}</p>
          </li>
        );
      })}
    </ol>
  );
}
