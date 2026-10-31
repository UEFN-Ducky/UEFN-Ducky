import { useEffect, useState } from "react";
import { useConfirmModal } from "../contexts/ConfirmModalContext";
import { getApi } from "../hooks/usePanelApi";
import { readLiveOverview } from "../hooks/useLiveOverview";
import type { PluginScopeChoice } from "../types/panel";
import { readActiveTeam, type ActiveTeam } from "./activeTeamStore";
import { ChoicePicker, type PickerChoice } from "./ScopePicker";

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function toChoice(c: PluginScopeChoice, team: ActiveTeam | null): PickerChoice {
  if (c.kind !== "shared") return { id: c.id, name: "Local", sub: "No team: this PC only", dot: "local" };
  const here = team?.current === c.id ? ` · ${plural(team.plugins.length, "plugin")}` : "";
  return { id: c.id, name: c.label, sub: `Team copy${here}`, dot: "shared" };
}

/**
 * The Team modal's sidebar picker, on every tab: which team this PC works in, or Local. Picking
 * one moves every plugin that can share there (`plugin_scope_set_all`); each plugin leaves its old
 * team's live group at once, so teammates stop seeing this PC right away. It asks first.
 */
export function TeamPicker({ team }: { team: ActiveTeam | null }) {
  const { confirm } = useConfirmModal();
  const [choices, setChoices] = useState<PluginScopeChoice[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");

  useEffect(() => {
    let live = true;
    void Promise.resolve(getApi()?.plugin_scope_choices?.()).then((got) => {
      if (live) setChoices(got?.choices?.length ? got.choices : [{ id: "personal", kind: "personal", label: "Local" }]);
    }).catch(() => {
      if (live) setChoices([{ id: "personal", kind: "personal", label: "Local" }]);
    });
    return () => {
      live = false;
    };
  }, []);

  const current = team?.current ?? "personal";
  const shown: PickerChoice = team?.shared
    ? { id: current, name: team.label, sub: `Team copy · ${plural(team.plugins.length, "plugin")}`, dot: "shared" }
    : { id: "personal", name: team ? "Local" : "Loading…", sub: team ? "No team: this PC only" : "", dot: "local" };

  const pick = async (id: string) => {
    const target = choices?.find((c) => c.id === id);
    if (!target) return;
    const name = target.kind === "shared" ? target.label : "Local";
    const leaving = team?.shared ? team.label : "";
    const ok = await confirm({
      title: target.kind === "shared" ? `Work in ${name}?` : "Keep everything on this PC?",
      message: [
        target.kind === "shared" ? `Every plugin that can share shows ${name}'s data.` : "Every plugin shows its Local data, only on this PC.",
        leaving ? `You leave ${leaving}'s live group now: teammates there stop seeing you at once.` : "",
      ].filter(Boolean).join(" "),
      confirmLabel: "Switch",
    });
    if (ok !== true) return;
    setBusy(true);
    setError("");
    setNote("");
    try {
      const out = await getApi()?.plugin_scope_set_all?.(id);
      if (!out) setError("This app can't switch teams yet.");
      else if (out.errors?.length) setError(out.errors.map((e) => `${e.plugin}: ${e.error}`).join(" "));
      else if (out.error) setError(out.error);
      if (out?.pending?.length) {
        setNote(`${plural(out.pending.length, "plugin")} ${out.pending.length === 1 ? "needs" : "need"} you to pick what to share first: open ${out.pending.length === 1 ? "it" : "them"} to finish.`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      await readActiveTeam();
      void readLiveOverview();
    }
  };

  return (
    <div className="team-picker">
      <div className="team-picker__label">Your team</div>
      <ChoicePicker label="Team" className="team-picker__btn" current={current} shown={shown}
        choices={choices ? choices.map((c) => toChoice(c, team)) : null}
        disabled={busy || choices === null || team === null} onPick={(id) => void pick(id)} />
      {busy ? <p className="team-picker__msg">Switching…</p> : null}
      {error ? <p className="team-picker__msg team-picker__msg--error" role="alert">{error}</p> : null}
      {note ? <p className="team-picker__msg">{note}</p> : null}
    </div>
  );
}
