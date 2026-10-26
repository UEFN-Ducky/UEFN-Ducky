/**
 * A tool's result drawn in the chat: the plugin it belongs to, what it made (videos play,
 * pictures open big), the plugin's own blocks, and an Open button that brings up the
 * plugin's tab only when the person presses it. Agents never pull a tab to the front.
 */
import { useMemo, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import { Icons } from "../../icons/Icons";
import { resolvePluginHeaderIcon } from "../../hooks/pluginHeaderActions";
import { usePluginContributions, type PluginContributions } from "../../hooks/usePluginContributions";
import { useMediaLinks, useToolOwners, type MediaLink, type ToolOwner } from "../../hooks/useToolView";
import { openPanelRoute } from "../../navigation/openPanelRoute";
import { requestOpenPluginUiTab } from "../../plugin-ui/openPluginUiTab";
import { unwrapCodingAgentTool } from "../../utils/unwrapCodingAgentTool";
import type { ActivityItem } from "../../utils/chatMessageGroups";
import { MediaLightbox, type LightboxItem } from "../MediaLightbox";
import { RichBlockList } from "../rich-content/RichBlockList";
import { bareToolName, hasViewContent, toolViewSpec, type ToolViewSpec } from "./toolView";

export type OpenTarget = { key: string; name: string; label: string; title: string; icon: ReactNode; open: () => void };

const PLAYABLE = new Set(["image", "svg", "video", "audio"]);

/** Where the card's Open button goes: ducky_ui_navigate's place, else the plugin's panel. */
export function openTargetFor(
  spec: ToolViewSpec | null,
  owner: ToolOwner | null,
  contrib: PluginContributions,
): OpenTarget | null {
  const nav = spec?.navigate;
  if (nav) {
    if (nav.route === "plugin_panel") {
      const [pid, panelId = ""] = nav.itemId.split(":");
      const panel = contrib.ui_panels.find((p) => p.plugin_id === pid && (!panelId || p.id === panelId));
      if (!pid || !panel) return null;
      return {
        key: `plugin:${pid}:${panel.id}`,
        name: nav.label,
        label: `Open ${nav.label}`,
        title: `Open ${nav.label} in a tab`,
        icon: resolvePluginHeaderIcon(panel.icon),
        open: () => requestOpenPluginUiTab(pid, panel.id, panel.title),
      };
    }
    return {
      key: `route:${nav.route}:${nav.itemId}`,
      name: nav.label,
      label: `Open ${nav.label}`,
      title: `Open ${nav.label}`,
      icon: <Icons.ChevronRight />,
      open: () => void openPanelRoute(nav.route, nav.itemId),
    };
  }
  if (!owner) return null;
  const panelId = spec?.openPanel || owner.panel_id;
  const panel = contrib.ui_panels.find((p) => p.plugin_id === owner.plugin_id && p.id === panelId);
  if (!panel) return null;
  return {
    key: `plugin:${owner.plugin_id}:${panel.id}`,
    name: owner.label,
    label: spec?.openLabel || `Open ${owner.label}`,
    title: `Open ${panel.title || owner.label} in a tab`,
    icon: resolvePluginHeaderIcon(panel.icon),
    open: () => requestOpenPluginUiTab(owner.plugin_id, panel.id, panel.title),
  };
}

/** The Open chip in a tool card's header row (a span: it sits inside the row's button). */
export function ToolOpenChip({ target }: { target: OpenTarget }) {
  const press = (e: MouseEvent | KeyboardEvent) => {
    e.stopPropagation();
    target.open();
  };
  return (
    <span
      className="tool-execution-card-open"
      role="button"
      tabIndex={0}
      title={target.title}
      aria-label={target.title}
      onClick={press}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          press(e);
        }
      }}
    >
      <span className="tool-execution-card-open-icon" aria-hidden="true">{target.icon}</span>
      <span>Open</span>
    </span>
  );
}

function mediaKind(link: MediaLink): string {
  return link.kind === "svg" ? "image" : link.kind;
}

function ToolMediaGrid({ links }: { links: MediaLink[] }) {
  const [shown, setShown] = useState<number | null>(null);
  const playable = links.filter((l) => PLAYABLE.has(l.kind));
  const files = links.filter((l) => !PLAYABLE.has(l.kind));
  const items: LightboxItem[] = playable.map((l) => ({ kind: mediaKind(l), name: l.name, url: l.url }));
  const single = playable.length === 1;
  return (
    <>
      {playable.length ? (
        <div className={`tool-inline-media-grid${single ? " tool-inline-media-grid--single" : ""}`}>
          {playable.map((link, i) => {
            const kind = mediaKind(link);
            if (kind === "video") {
              return (
                <div key={link.url} className="tool-inline-media tool-inline-media--video">
                  <video src={link.url} controls preload="metadata" />
                  <button type="button" className="tool-inline-media-enlarge" title="Open big" aria-label={`Open ${link.name} big`} onClick={() => setShown(i)}>
                    <Icons.FullScreen />
                  </button>
                </div>
              );
            }
            if (kind === "audio") {
              return (
                <div key={link.url} className="tool-inline-media tool-inline-media--audio">
                  <span className="tool-inline-media-name">{link.name}</span>
                  <audio src={link.url} controls preload="metadata" />
                </div>
              );
            }
            return (
              <button key={link.url} type="button" className="tool-inline-media tool-inline-media--image" title={`${link.name} · open big`} onClick={() => setShown(i)}>
                <img src={link.url} alt={link.name} loading="lazy" draggable={false} />
              </button>
            );
          })}
        </div>
      ) : null}
      {files.length ? (
        <div className="tool-inline-files">
          {files.map((link) => (
            <a key={link.url} className="tool-inline-file" href={link.url} download={link.name} title={link.path}>
              <Icons.File />
              <span>{link.name}</span>
            </a>
          ))}
        </div>
      ) : null}
      <MediaLightbox items={items} index={shown} onClose={() => setShown(null)} />
    </>
  );
}

interface ToolInlineViewProps {
  spec: ToolViewSpec;
  /** Named over the view when it stands apart from its card (under a folded group). */
  owner?: ToolOwner | null;
}

export function ToolInlineView({ spec, owner = null }: ToolInlineViewProps) {
  const links = useMediaLinks(spec.media);
  const caption = [owner?.label, spec.title].filter(Boolean).join(" · ");
  if (!links.length && !spec.blocks.length && !spec.title) return null;
  return (
    <div className="tool-inline-view">
      {caption ? <div className="tool-inline-view-head">{caption}</div> : null}
      {links.length ? <ToolMediaGrid links={links} /> : null}
      {spec.blocks.length ? (
        <div className="tool-inline-view-blocks">
          <RichBlockList blocks={spec.blocks} />
        </div>
      ) : null}
    </div>
  );
}

type ToolCall = { id: string; name: string; args: Record<string, unknown>; result: string; running: boolean; ok: boolean };

function callsOf(items: ActivityItem[]): ToolCall[] {
  const out: ToolCall[] = [];
  for (const item of items) {
    if (item.kind !== "tool") continue;
    const tool = item.result?.tool ?? item.intent.tool;
    const status = item.result?.tool?.status ?? item.intent.tool?.status;
    const running = status ? status === "pending" || status === "running" : !item.result;
    const rawArgs = item.result?.tool?.arguments ?? item.intent.tool?.arguments ?? {};
    const call = unwrapCodingAgentTool(tool?.name ?? "", rawArgs && typeof rawArgs === "object" && !Array.isArray(rawArgs) ? (rawArgs as Record<string, unknown>) : {});
    const ok = status === "success" || item.result?.role === "success";
    out.push({ id: item.id, name: bareToolName(call.name), args: call.arguments, result: String(item.result?.tool?.result ?? ""), running, ok });
  }
  return out;
}

const STRIP_VIEWS = 3;

/**
 * Under a folded tools group: one Open button per plugin the agent used (spinning while
 * one of its calls runs) and what the latest calls made, so nothing needs unfolding.
 */
export function ToolViewStrip({ items }: { items: ActivityItem[] }) {
  const calls = useMemo(() => callsOf(items), [items]);
  const names = useMemo(() => [...new Set(calls.map((c) => c.name).filter(Boolean))].sort(), [calls]);
  const owners = useToolOwners(names);
  const contrib = usePluginContributions();
  const specs = useMemo(
    () => calls.map((c) => ({ call: c, spec: !c.running && c.ok ? toolViewSpec(c.name, c.args, c.result) : null })),
    [calls],
  );
  const chips = useMemo(() => {
    const out: Array<OpenTarget & { busy: boolean }> = [];
    const seen = new Map<string, number>();
    for (const { call, spec } of specs) {
      const target = openTargetFor(spec, owners[call.name] ?? null, contrib);
      if (!target) continue;
      const at = seen.get(target.key);
      if (at === undefined) {
        seen.set(target.key, out.length);
        out.push({ ...target, busy: call.running });
      } else if (call.running) {
        out[at] = { ...out[at], busy: true };
      }
    }
    return out;
  }, [specs, owners, contrib]);
  const views = useMemo(
    () => specs.filter(({ spec }) => hasViewContent(spec)).slice(-STRIP_VIEWS),
    [specs],
  );
  if (!chips.length && !views.length) return null;
  return (
    <div className="tool-view-strip">
      {chips.length ? (
        <div className="tool-view-strip-chips" role="group" aria-label="Open in a tab">
          {chips.map((chip) => (
            <button key={chip.key} type="button" className="tool-view-strip-chip" title={chip.title} onClick={chip.open}>
              <span className="tool-view-strip-chip-icon" aria-hidden="true">
                {chip.busy ? <span className="tool-view-strip-spin"><Icons.Spinner /></span> : chip.icon}
              </span>
              <span>{chip.busy ? `${chip.name} · working…` : chip.label}</span>
            </button>
          ))}
        </div>
      ) : null}
      {views.map(({ call, spec }) =>
        spec ? <ToolInlineView key={call.id} spec={spec} owner={owners[call.name] ?? null} /> : null,
      )}
    </div>
  );
}
