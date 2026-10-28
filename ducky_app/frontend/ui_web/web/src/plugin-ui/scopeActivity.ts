import {
  dismissBackgroundJob,
  getBackgroundJobs,
  onBackgroundJobAction,
  upsertBackgroundJob,
} from "../hooks/backgroundActivity";
import { getTeamScope, subscribeTeamScope, type TeamScope } from "./teamScopeStore";

/** The Team modal listens for this to open on a section (`detail.section`). */
export const OPEN_TEAM_MODAL_EVENT = "ducky:open-team-modal";
const PREFIX = "team-sync:";
/** A finished round stays in the tray this long, then leaves on its own. */
export const DONE_LINGER_MS = 3000;

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;

function workDetail(scope: TeamScope): string {
  const sending = scope.status.pending ?? 0;
  const getting = scope.status.changes?.incoming ?? 0;
  if (sending) return `Sending ${plural(sending, "change")}`;
  if (getting) return `Getting ${plural(getting, "change")}`;
  return "Checking for changes";
}

/** Mirror the visible team copy's sync into the activity tray: a working row while it syncs,
 *  a done row that clears itself, an error row while conflicts wait. Returns the uninstall.
 *  ponytail: the store only carries the visible pane, so a background team's round shows
 *  once its pane is visible; publish every pane's state if hidden teams need rows. */
export function installScopeActivity(): () => void {
  const timers = new Map<string, ReturnType<typeof setTimeout>>();
  let lastId = "";

  const finish = (id: string) => {
    const job = getBackgroundJobs().find((j) => j.id === id);
    if (!job || job.phase !== "working") return;
    const now = Date.now();
    upsertBackgroundJob({ id, phase: "done", title: job.title.replace(/ · Syncing$/, " · Up to date"), detail: "Done", endedAt: now, ts: now });
    clearTimeout(timers.get(id));
    timers.set(id, setTimeout(() => {
      timers.delete(id);
      if (getBackgroundJobs().find((j) => j.id === id)?.phase === "done") dismissBackgroundJob(id);
    }, DONE_LINGER_MS));
  };

  const update = () => {
    const scope = getTeamScope();
    const space = scope?.shared ? scope.status.scope?.spaceId || "" : "";
    const id = space ? PREFIX + space : "";
    if (lastId && lastId !== id) finish(lastId);
    lastId = id;
    if (!scope || !id) return;
    const label = scope.status.scope?.label || scope.plugin;
    const prev = getBackgroundJobs().find((j) => j.id === id);
    const now = Date.now();
    if (scope.conflicts > 0) {
      clearTimeout(timers.get(id));
      const title = `${label} · ${plural(scope.conflicts, "conflict")}`;
      if (prev?.phase !== "error" || prev.title !== title) {
        upsertBackgroundJob({ id, source: "team", title, detail: "Open Team to resolve", action: "Resolve", phase: "error", endedAt: now, ts: now });
      }
      return;
    }
    if (prev?.phase === "error") dismissBackgroundJob(id);
    if (scope.syncing || scope.busy) {
      clearTimeout(timers.get(id));
      const detail = workDetail(scope);
      if (prev?.phase !== "working") {
        upsertBackgroundJob({ id, source: "team", title: `${label} · Syncing`, detail, action: "", phase: "working", startedAt: now, endedAt: 0, ts: now });
      } else if (prev.detail !== detail) upsertBackgroundJob({ id, detail, ts: now });
      return;
    }
    finish(id);
  };

  const stopScope = subscribeTeamScope(update);
  const stopAction = onBackgroundJobAction((jobId) => {
    if (!jobId.startsWith(PREFIX)) return;
    window.dispatchEvent(new CustomEvent(OPEN_TEAM_MODAL_EVENT, { detail: { section: "conflicts" } }));
  });
  update();
  return () => {
    stopScope();
    stopAction();
    for (const t of timers.values()) clearTimeout(t);
    timers.clear();
  };
}
