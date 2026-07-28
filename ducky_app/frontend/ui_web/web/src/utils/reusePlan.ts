import { getApi } from "../hooks/usePanelApi";
import { requestOpenPlanTab } from "../navigation/openPlanTab";
import type { PlanListItem } from "../types/panel";

function normRoot(path: string | null | undefined): string {
  return (path || "").trim().replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

/** Ensure the panel's active project matches `targetRoot` (no-op for app-data ""). */
export async function ensureProjectRoot(targetRoot: string): Promise<void> {
  const api = getApi();
  if (!api) throw new Error("Panel API not ready");
  const target = (targetRoot || "").trim();
  if (!target) return; // app-data plans — stay on current project
  const settings = await api.get_settings();
  const current = (settings.uefn_project_root || "").trim();
  if (normRoot(current) === normRoot(target)) return;
  await api.set_project_root(target);
}

export async function openPlanFromCatalog(item: PlanListItem): Promise<void> {
  const api = getApi();
  if (!api) throw new Error("Panel API not ready");
  const root = item.project_root ?? "";
  if (root) {
    await ensureProjectRoot(root);
  }
  requestOpenPlanTab({
    chatId: item.chat_id,
    title: item.title,
    projectRoot: root ? undefined : "",
  });
}
