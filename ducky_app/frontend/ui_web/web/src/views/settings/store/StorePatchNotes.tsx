import { useEffect, useState } from "react";
import { getApi } from "../../../hooks/usePanelApi";
import type { DuckyOSStorePatchNote } from "../../../types/panel";
import { PatchNotesList } from "./PatchNotesList";

type Props = {
  slug: string;
};

export function StorePatchNotes({ slug }: Props) {
  const [notes, setNotes] = useState<DuckyOSStorePatchNote[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    setNotes(null);
    const api = getApi();
    if (!api || typeof api.duckyos_store_versions !== "function" || !slug) {
      setNotes([]);
      return () => {
        cancelled = true;
      };
    }
    void api.duckyos_store_versions(slug).then((res) => {
      if (cancelled) return;
      setNotes(Array.isArray(res.versions) ? res.versions : []);
    }).catch(() => {
      if (!cancelled) setNotes([]);
    });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  return (
    <div className="ds-panel ds-patch-notes">
      <h3 className="ds-panel-title">Patch notes</h3>
      <PatchNotesList notes={notes} />
    </div>
  );
}
