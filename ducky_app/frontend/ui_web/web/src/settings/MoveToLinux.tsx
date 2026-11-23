import { useState } from "react";
import { getApi } from "../hooks/usePanelApi";

/** Settings > App Data > Move to Linux: one passphrase-locked archive of chats, settings,
 * plans, workflows and keys (`backend/store/migrate_export.py`, route `move_to_linux`). */

type Mode = "export" | "import";
type Result = {
  ok: boolean;
  error?: string;
  path?: string;
  counts?: Record<string, number>;
  summary?: string;
};
type MoveApi = {
  move_to_linux: (action: Mode, path: string, passphrase: string) => Promise<Result>;
  pick_workflow_folder: () => Promise<{ ok: boolean; folder: string; error?: string }>;
  pick_workflow_files: (accept?: string, multiple?: boolean) => Promise<{ ok: boolean; files: { path: string }[]; error?: string }>;
};

export const MIN_PASSPHRASE = 8; // migrate_export.MIN_PASSPHRASE

/** What is wrong with the passphrase fields, or "" when the action can run. */
export function passphraseProblem(mode: Mode, pass: string, confirm: string): string {
  if (pass.length < MIN_PASSPHRASE) return `Use at least ${MIN_PASSPHRASE} characters.`;
  if (mode === "export" && pass !== confirm) return "The passphrases don't match.";
  return "";
}

export function MoveToLinux() {
  const [mode, setMode] = useState<Mode>("export");
  const [pass, setPass] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const problem = passphraseProblem(mode, pass, confirm);

  const run = async () => {
    const api = getApi() as unknown as MoveApi | null;
    if (!api || problem) return;
    setError("");
    setDone("");
    let path = "";
    if (mode === "export") {
      const picked = await api.pick_workflow_folder();
      path = picked.folder;
      if (!picked.ok) return setError(picked.error || "Couldn't open the folder picker.");
    } else {
      const picked = await api.pick_workflow_files("any", false);
      path = picked.files?.[0]?.path || "";
      if (!picked.ok) return setError(picked.error || "Couldn't open the file picker.");
    }
    if (!path) return; // picker cancelled
    setBusy(true);
    try {
      const out = await api.move_to_linux(mode, path, pass);
      if (!out.ok) setError(out.error || "Something went wrong.");
      else if (mode === "export") setDone(`Saved ${out.path} (${out.counts?.chats ?? 0} chats, ${out.counts?.keys ?? 0} keys).`);
      else setDone(out.summary || "Imported.");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="settings-section move-to-linux" aria-label="Move to Linux">
      <h3>Move to Linux</h3>
      <p>Take your chats, settings, plans, workflows and API keys to Ducky on another PC. The archive is locked with a passphrase.</p>
      <div role="tablist">
        <button type="button" role="tab" aria-selected={mode === "export"} className="settings-btn" onClick={() => setMode("export")}>
          Export for Linux
        </button>
        <button type="button" role="tab" aria-selected={mode === "import"} className="settings-btn" onClick={() => setMode("import")}>
          Import from Windows
        </button>
      </div>
      <input
        className="settings-input"
        type="password"
        aria-label="Passphrase"
        placeholder="Passphrase"
        autoComplete="new-password"
        value={pass}
        onChange={(e) => setPass(e.target.value)}
      />
      {mode === "export" && (
        <input
          className="settings-input"
          type="password"
          aria-label="Repeat passphrase"
          placeholder="Repeat passphrase"
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
      )}
      {pass && problem && <p className="settings-hint">{problem}</p>}
      <button type="button" className="settings-btn" disabled={busy || !!problem} onClick={() => void run()}>
        {busy ? (mode === "export" ? "Exporting…" : "Importing…") : mode === "export" ? "Export…" : "Import…"}
      </button>
      {busy && <progress aria-label="Working" />}
      {error && <p role="alert" className="settings-error">{error}</p>}
      {done && <p role="status">{done}</p>}
    </section>
  );
}
