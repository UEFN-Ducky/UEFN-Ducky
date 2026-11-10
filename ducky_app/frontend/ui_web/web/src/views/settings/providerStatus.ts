/** A provider row is connected through a signed-in coding agent of the same plugin. Keyed by
 *  plugin, never by provider id: Google's provider is "gemini", its plugin (and Gemini CLI's) "google". */
export function connectedViaAgent(
  row: { id: string; plugin_id?: string },
  agentOkByPlugin: Record<string, boolean>,
): boolean {
  return !!agentOkByPlugin[(row.plugin_id || row.id).trim().toLowerCase()];
}
