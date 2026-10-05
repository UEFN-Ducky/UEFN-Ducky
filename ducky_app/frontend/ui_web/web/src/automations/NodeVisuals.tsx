import { useEffect, useState } from "react";
import { getApi } from "../hooks/usePanelApi";
import { resolvePluginHeaderIcon } from "../hooks/pluginHeaderActions";
import { BUNDLED_DUCKIES, DEFAULT_BUNDLED_DUCKY_STYLE } from "../generated/bundledDuckies";
import { useDuckyCatalogOptional } from "../components/ducky/DuckyCatalogContext";
import type { AutomationGraphNodeDto, AutomationNodeDto } from "../types/panel";
import { Icons } from "../icons/Icons";

export function isEndNode(node: AutomationGraphNodeDto) {
  return node.type === "pipeline.finish" || node.type === "flow.end" || node.type === "flow.output";
}

export type NodeRole = "starter" | "end" | "agent" | "function" | "logic" | "action" | "input";

/** The title bar colour, Unreal style: events red, calls blue, functions green, flow control grey. */
export function nodeRole(node: AutomationGraphNodeDto, meta?: AutomationNodeDto): NodeRole {
  if (isEndNode(node) || node.type === "util.preview") return "end";
  if (meta?.role === "input" || node.type.startsWith("input.")) return "input";
  if (meta?.role === "logic" || node.type.startsWith("logic.") || node.type === "text.template") return "logic";
  if (node.type === "llm.ask") return "agent";
  if (meta?.role === "agent" || meta?.role === "function" || meta?.role === "end") return meta.role;
  if (meta?.role === "starter" || node.type.startsWith("start.") || node.type === "flow.input") return "starter";
  if (node.type === "pipeline.agent" || meta?.group === "Agents" || meta?.group === "Duckies") return "agent";
  if (node.type === "workflow.call") return "function";
  if (node.type.startsWith("flow.")) return "logic";
  return "action";
}

/** The line under the title: the node's own description, else what it is set to do. */
export function nodeSummary(node: AutomationGraphNodeDto, meta?: AutomationNodeDto) {
  if (node.description) return node.description;
  if (node.type === "tool.call") return String(node.config.name || "Choose a tool");
  return meta?.description || "";
}

export function nodeLabel(node: AutomationGraphNodeDto, meta?: AutomationNodeDto) {
  if (node.type === "start.chat" && (!node.label || node.label === "Chat")) return "Chat input";
  if (node.type === "pipeline.finish" && (!node.label || node.label === "Finish")) return "Return to user";
  return node.label || meta?.label || node.type;
}

export function useNodeFaces(enabled: boolean) {
  const [faces, setFaces] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const refresh = async () => {
      const api = getApi();
      const [library, chats] = await Promise.allSettled([api?.list_agent_profiles?.(), api?.list_all_conversations?.(true)]);
      if (cancelled) return;
      const next: Record<string, string> = {};
      if (library.status === "fulfilled") {
        for (const profile of [...(library.value?.template_profiles || []), ...(library.value?.profiles || [])]) {
          next[profile.id] = profile.ducky_style;
          next[profile.name.toLowerCase()] = profile.ducky_style;
        }
      }
      if (chats.status === "fulfilled") for (const chat of chats.value || []) next[`chat:${chat.id}`] = chat.ducky_style || "";
      setFaces(next);
    };
    void refresh();
    window.addEventListener("focus", refresh);
    return () => { cancelled = true; window.removeEventListener("focus", refresh); };
  }, [enabled]);
  return faces;
}

const EMOJI: Record<string, string> = {
  "start.chat": "💬", "start.manual": "▶️", "start.cron": "⏰",
  "pipeline.finish": "📤", "flow.end": "🏁", "flow.wait": "⏳",
  "flow.branch": "🔀", "flow.foreach": "🔁", "flow.repeat": "🔂", "tool.call": "🛠️",
  "fortnite.servers": "🛰️", "notify.message": "📨",
  "flow.input": "📥", "flow.output": "↩️", "workflow.call": "🧩",
  "uefn.open_project": "📂", "uefn.launch": "🚀", "uefn.close": "⏹️",
  "uefn.restart": "🔄", "uefn.wait_ready": "⏳", "uefn.wait_window": "🖥️",
  "uefn.check": "🩺", "uefn.game.start": "🎮", "uefn.game.stop": "⏹", "uefn.player.wait": "🧍", "uefn.log.expect": "🔎",
  "input.text": "🔤", "input.number": "🔢", "input.boolean": "✅", "input.json": "🧾", "input.image": "🖼️", "input.images": "🗂️",
  "input.audio": "🎵", "input.video": "🎬", "input.mesh": "🧊", "input.pdf": "📄", "input.svg": "✒️", "input.file": "📎",
  "logic.if": "❓", "logic.expression": "🧮", "logic.compare": "⚖️", "llm.ask": "🤖", "text.template": "📝", "util.preview": "👁️",
  "llm.vision": "🧐", "llm.extract": "🧾", "llm.translate": "🌍", "llm.pick": "🔎",
  "image.generate": "🎨", "image.edit": "🪄", "image.remove_bg": "✂️", "image.upscale": "🔍", "image.resize": "↔️", "image.crop": "🔲",
  "image.convert": "🔄", "image.split_alpha": "🌗", "image.combine_alpha": "🌓", "image.split_channels": "🎛️", "image.combine_channels": "🎚️",
  "image.concat": "🧩", "image.text": "🔤",
  "mesh.generate": "🧊", "mesh.from_image": "📸", "mesh.multi_view": "🎞️", "mesh.retexture": "🖌️", "mesh.remesh": "🕸️", "mesh.uv_unwrap": "🗺️",
  "mesh.convert": "🔁", "mesh.repair": "🩹", "mesh.bake": "🍞", "mesh.render": "📷", "mesh.rig": "🦴", "mesh.animate": "🕺",
  "mesh.info": "📏", "mesh.fit_box": "📦", "mesh.set_origin": "📍", "mesh.origin_text": "🎯", "mesh.auto_scale": "📐", "mesh.rotate": "🔃",
  "mesh.textures_extract": "🗂️", "mesh.textures_apply": "🧵",
  "blender.open": "🟠", "blender.render": "🎬", "blender.export": "📤", "uefn.import": "🚚",
  "list.make": "📋", "list.get": "👉", "list.count": "🔢", "list.join": "🔗", "list.filter": "🧹", "list.map": "🔀",
  "pdf.text": "📄", "pdf.images": "🖼️", "util.save_file": "💾",
  "code.js": "💻",
};

/** The built-in a custom code node was made from, as a node of that type (for its icon). */
export function basedOnNode(node: AutomationGraphNodeDto | undefined): AutomationGraphNodeDto | null {
  if (node?.type !== "code.js") return null;
  const raw = node.config.based_on as { type?: unknown; config?: unknown } | undefined;
  const type = typeof raw?.type === "string" ? raw.type : "";
  return type ? { ...node, type, config: raw?.config && typeof raw.config === "object" ? raw.config as Record<string, unknown> : {} } : null;
}

/** `basedMeta`: a custom code node made from a built-in keeps that built-in's icon. */
export function NodeIcon({ meta, node, faces = {}, basedMeta }: { meta?: AutomationNodeDto; node?: AutomationGraphNodeDto; faces?: Record<string, string>; basedMeta?: AutomationNodeDto }) {
  const catalog = useDuckyCatalogOptional();
  if (node?.icon) return <span className="aw-node-icon" aria-hidden>{resolvePluginHeaderIcon(node.icon)}</span>;  // picked in its details
  const based = basedOnNode(node);
  if (based) return <NodeIcon meta={basedMeta} node={based} faces={faces} />;
  if (meta?.plugin_id && meta.icon) return <span className="aw-node-icon" aria-hidden>{resolvePluginHeaderIcon(meta.icon)}</span>;
  if (meta?.group === "Agents" || meta?.group === "Duckies" || node?.type === "pipeline.agent") {
    const assignment = String(node?.config.ducky || node?.config.profile_id || "");
    const style = faces[assignment] || faces[assignment.toLowerCase()] || catalog?.defaultStyle || DEFAULT_BUNDLED_DUCKY_STYLE;
    const url = catalog?.resolveUrl(style) || BUNDLED_DUCKIES.find((row) => row.id === style)?.url || BUNDLED_DUCKIES[0]?.url;
    return <span className="aw-node-icon" aria-hidden><img src={url} alt="" draggable={false} /></span>;
  }
  return <span className="aw-node-icon" aria-hidden>{resolvePluginHeaderIcon(meta?.icon || EMOJI[node?.type || meta?.type || ""] || (meta?.role === "starter" ? "⚡" : "🧩"))}</span>;
}

/** A group's icon: the one picked in its details, else a box. */
export function GroupIcon({ icon }: { icon?: string }) {
  return <span className="aw-node-icon" aria-hidden>{icon ? resolvePluginHeaderIcon(icon) : <Icons.Box />}</span>;
}
