import { Icons } from "../../icons/Icons";
import { usePluginContributions } from "../../hooks/usePluginContributions";
import { redoInstalledPluginWalkthrough } from "./store/pluginStoreActions";

/** Replay control for a plugin's product walkthrough (Settings section title). */
export function PluginWalkthroughReplayButton({
  pluginId,
  label,
}: {
  pluginId: string;
  label?: string;
}) {
  const contrib = usePluginContributions();
  const pid = pluginId.trim().toLowerCase();
  if (!pid) return null;
  const has = contrib.walkthroughs.some((row) => {
    const id = String(row.plugin_id || row.id || "")
      .trim()
      .toLowerCase()
      .replace(/^plugin\./, "");
    return id === pid;
  });
  if (!has) return null;
  const name = (label || pid).trim() || pid;
  return (
    <button
      type="button"
      className="plugin-walkthrough-replay"
      title={`Replay ${name} walkthrough`}
      aria-label={`Replay ${name} walkthrough`}
      onClick={() => void redoInstalledPluginWalkthrough(pid)}
    >
      <Icons.Replay />
    </button>
  );
}
