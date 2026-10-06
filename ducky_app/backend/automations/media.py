"""Image and 3D nodes: each node type has a table of backends — a tool an installed
plugin registers (3D AI Studio, Meshy), or an AI gateway plugin's own image node
(Google, OpenAI) — and the node calls the one picked in its details, waits for it, and
turns the files it downloads into file refs on its output pins. Text to Image lists only
what the plugins turned on here declare (contributes.automations.image_generators) and
the gateways' image nodes. A backend whose plugin is off, or (gateways) has no key,
can't be picked or run.

Paid backends keep the plugins' spend lock: a node only spends credits (or, on a
gateway, the person's own API key) when its "Spend credits" switch is on (a person
turns it on; an AI asks them first)."""

from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path
from typing import Any, Callable

from backend.automations.files import file_ref, is_file_ref, kind_of, with_url

Args = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]

STUDIO = "3D AI Studio"
MESHY = "Meshy"
# AI gateways whose image node we know by name: plugin id → (backend id, label, model, key, name).
# Any other gateway plugin that registers "<id>.image" is listed too, on its own default model.
GATEWAY_IMAGES = {
    "google": ("google_imagen", "Imagen 4", "imagen-4.0-generate-001", "gemini", "Google"),
    "openai": ("openai_image", "GPT Image 1", "gpt-image-1", "openai", "OpenAI"),
}
_MAX_INLINE_MODEL = 25 * 1024 * 1024
# Main file when a generator downloads several (a model in many formats, textures…).
_PREFERRED = {
    "image": (".png", ".webp", ".jpg", ".jpeg"),
    "mesh": (".glb", ".fbx", ".obj", ".usdz", ".gltf", ".stl"),
}


# --------------------------------------------------------------------------- inputs


def path_of(value: Any) -> str:
    """A local path or URL from a wired value (a file ref, a list's first item, text)."""
    if isinstance(value, list):
        return path_of(value[0]) if value else ""
    if isinstance(value, dict):
        return str(value.get("path") or value.get("url") or "")
    return str(value or "").strip()


def _need_file(inputs: dict[str, Any], pin: str, what: str) -> str:
    path = path_of(inputs.get(pin))
    if not path:
        raise ValueError(f"Nothing in {what}: wire one in or pick it in the details.")
    if not path.lower().startswith(("http://", "https://", "data:")) and not Path(path).is_file():
        raise ValueError(f"{what} {Path(path).name} isn't on this PC any more.")
    return path


def _need_text(inputs: dict[str, Any], pin: str, what: str) -> str:
    raw = inputs.get(pin)
    text = (raw if isinstance(raw, str) else json.dumps(raw) if isinstance(raw, (dict, list)) else str(raw if raw is not None else "")).strip()
    if not text:
        raise ValueError(f"Nothing in {what}: wire text in or type it in the details.")
    return text


def _number(inputs: dict[str, Any], cfg: dict[str, Any], key: str, default: float) -> float:
    for raw in (inputs.get(key), cfg.get(key)):
        try:
            if raw not in (None, ""):
                return float(raw)
        except (TypeError, ValueError):
            continue
    return default


def _model_source(inputs: dict[str, Any]) -> dict[str, str]:
    """How a Meshy edit finds the model: the Meshy task that made it, else its link,
    else the file itself (sent inline)."""
    ref = inputs.get("mesh")
    if isinstance(ref, dict) and ref.get("provider") == MESHY and ref.get("task_id"):
        return {"input_task_id": str(ref["task_id"])}
    if isinstance(ref, dict) and str(ref.get("remote") or "").startswith("https://"):
        return {"model_url": str(ref["remote"])}
    path = _need_file(inputs, "mesh", "3D model")
    if path.lower().startswith(("http://", "https://", "data:")):
        return {"model_url": path}
    data = Path(path).read_bytes()
    if len(data) > _MAX_INLINE_MODEL:
        raise ValueError(f"{Path(path).name} is over 25 MB; Meshy can only edit models it made or smaller files.")
    return {"model_url": "data:application/octet-stream;base64," + base64.b64encode(data).decode("ascii")}


def _prompt(inputs: dict[str, Any], _cfg: dict[str, Any]) -> dict[str, Any]:
    return {"prompt": _need_text(inputs, "prompt", "Prompt")}


def _image(inputs: dict[str, Any], _cfg: dict[str, Any]) -> dict[str, Any]:
    return {"image": _need_file(inputs, "image", "Image")}


def _image_and_prompt(inputs: dict[str, Any], _cfg: dict[str, Any]) -> dict[str, Any]:
    return {"image": _need_file(inputs, "image", "Image"), "prompt": _need_text(inputs, "prompt", "Prompt")}


def _meshy_from_image(inputs: dict[str, Any], _cfg: dict[str, Any]) -> dict[str, Any]:
    args: dict[str, Any] = {"image": _need_file(inputs, "image", "Image")}
    hint = inputs.get("prompt")
    if isinstance(hint, str) and hint.strip():
        args["texture_prompt"] = hint.strip()
    return args


def _remesh(inputs: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    return {**_model_source(inputs), "target_polycount": int(_number(inputs, cfg, "polycount", 30000)),
            "topology": "quad" if str(cfg.get("topology") or "") == "quad" else "triangle"}


def _retexture(inputs: dict[str, Any], _cfg: dict[str, Any]) -> dict[str, Any]:
    style = path_of(inputs.get("style_image"))
    hint = inputs.get("prompt")
    if not style and not (isinstance(hint, str) and hint.strip()):
        raise ValueError("Nothing in Style: type a style prompt or wire a style picture in.")
    args: dict[str, Any] = _model_source(inputs)
    if isinstance(hint, str) and hint.strip():
        args["text_style_prompt"] = hint.strip()
    if style:
        args["image_style"] = _need_file(inputs, "style_image", "Style image")
    return args


def _rig(inputs: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    return {**_model_source(inputs), "height_meters": _number(inputs, cfg, "height", 1.7)}


def action_ids(inputs: dict[str, Any], cfg: dict[str, Any]) -> list[int]:
    """The animations to make: '12, 35 101' or a list → [12, 35, 101] (at most 10)."""
    raw = inputs.get("actions")
    if raw in (None, "", []):
        raw = cfg.get("actions") if cfg.get("actions") not in (None, "") else cfg.get("action_id")
    items = raw if isinstance(raw, list) else str(raw or "").replace(",", " ").split()
    out: list[int] = []
    for item in items:
        try:
            value = int(float(item))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Animations are numbers from the Meshy library, not {item!r}.") from exc
        if value <= 0:
            raise ValueError("Walking and running come with the Rig node; animations here are numbers from 1 up (Idle 1 is 11).")
        if value not in out:
            out.append(value)
    if not out:
        raise ValueError("Set the animation numbers in the details (the Meshy animation library lists them).")
    if len(out) > 10:
        raise ValueError("Ten animations at most per node.")
    return out


def _animate(inputs: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    ref = inputs.get("mesh")
    if not (isinstance(ref, dict) and ref.get("provider") == MESHY and ref.get("stage") == "rig" and ref.get("task_id")):
        raise ValueError("Animate needs the model straight from a Rig node (Meshy).")
    return {"rig_task_id": str(ref["task_id"]), "action_id": action_ids(inputs, cfg)[0]}


def _web_link(inputs: dict[str, Any], pin: str = "mesh", what: str = "3D model") -> str:
    """A web link to the model: 3D AI Studio's tools only take links, not files."""
    ref = inputs.get(pin)
    if isinstance(ref, dict) and str(ref.get("remote") or "").startswith("https://"):
        return str(ref["remote"])
    path = path_of(ref)
    if path.lower().startswith("https://"):
        return path
    if not path:
        raise ValueError(f"Nothing in {what}: wire a model in.")
    raise ValueError(f"3D AI Studio needs a model one of the 3D generators made (it has a web link); {Path(path).name} is only on this PC. Use the Meshy backend for local files.")


def _studio_model(inputs: dict[str, Any], _cfg: dict[str, Any]) -> dict[str, Any]:
    return {"model_url": _web_link(inputs)}


def _multi_view(inputs: dict[str, Any], _cfg: dict[str, Any]) -> dict[str, Any]:
    raw = inputs.get("images")
    paths = [path_of(item) for item in (raw if isinstance(raw, list) else [raw]) if path_of(item)]
    if len(paths) < 2:
        raise ValueError("Multi-view needs at least 2 pictures of the same thing (front, side, back…).")
    for path in paths:
        if not path.lower().startswith(("http://", "https://")) and not Path(path).is_file():
            raise ValueError(f"{Path(path).name} isn't on this PC any more.")
    return {"images": json.dumps(paths[:4])}


def _convert_meshy(inputs: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    return {**_model_source(inputs), "target_formats": _format(cfg, ("fbx", "glb", "obj", "usdz", "stl"))}


def _convert_studio(inputs: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    return {"model_url": _web_link(inputs), "output_format": _format(cfg, ("fbx", "obj", "stl", "ply"))}


def _format(cfg: dict[str, Any], allowed: tuple[str, ...]) -> str:
    fmt = str(cfg.get("format") or "fbx").strip().lower()
    if fmt not in allowed:
        raise ValueError(f"This backend makes {', '.join(allowed)}; pick one of those or another backend.")
    return fmt


def _bake(inputs: dict[str, Any], _cfg: dict[str, Any]) -> dict[str, Any]:
    return {"high_poly_url": _web_link(inputs, "high", "High-poly model"), "low_poly_url": _web_link(inputs, "low", "Low-poly model")}


def _studio(tool: str, label: str, credits: int, args: Args, bid: str = "") -> dict[str, Any]:
    return {"id": bid or tool.removeprefix("studio3d_image_").removeprefix("studio3d_"), "label": label, "plugin": STUDIO,
            "plugin_id": "studio3d", "tool": tool, "credits": credits, "args": args}


def _meshy(tool: str, label: str, credits: int, args: Args, bid: str = "", stage: str = "") -> dict[str, Any]:
    row = {"id": bid or tool, "label": label, "plugin": MESHY, "plugin_id": "meshy", "tool": tool, "credits": credits, "args": args}
    return {**row, "stage": stage} if stage else row


def _gateway(plugin_id: str, node: str) -> dict[str, Any]:
    """An AI gateway plugin's own image node, billed to the person's API key (no credits)."""
    known = GATEWAY_IMAGES.get(plugin_id)
    name = _manifest_label(plugin_id) or (known[4] if known else plugin_id.title())
    bid, _label, model, key, _name = known or (f"{plugin_id}_image", f"{name} image", "", plugin_id, name)
    return {"id": bid, "label": f"{name} image", "plugin": name, "plugin_id": plugin_id, "node": node, "key": key, "model": model,
            "config_fields": gateway_image_fields(node), "credits": 0, "paid": True, "args": _prompt}


def gateway_image_fields(node: str) -> list[dict[str, Any]]:
    """Use the image handler's own controls, never the gateway's chat/agent models."""
    try:
        from backend.uefn_plugins.host import get_ui_contributions

        spec = next((row for row in get_ui_contributions().get("automations_nodes", []) if row.get("id") == node), {})
        fields = [dict(field) for field in spec.get("config_fields", []) if field.get("id") != "prompt"]
        for field in fields:
            if field.get("type") == "model":
                field["type"] = "text"  # no image capability list: allow an explicit image model ID
        return fields or [{"id": "model", "label": "Image model", "type": "text"}]
    except Exception:
        return [{"id": "model", "label": "Image model", "type": "text"}]


def gateway_image_nodes() -> list[tuple[str, str]]:
    """(node type, plugin id) of every installed plugin's "<id>.image" node."""
    try:
        from backend.automations.plugin import node_types

        return [(ntype, pid) for ntype, pid in node_types() if ntype.endswith(".image") and pid]
    except Exception:
        return []


def plugin_image_generators() -> list[dict[str, Any]]:
    """What the plugins turned on here declare under contributes.automations.image_generators."""
    try:
        from backend.uefn_plugins.host import image_generators

        return image_generators()
    except Exception:
        return []


def _declared(row: dict[str, Any]) -> dict[str, Any]:
    """A plugin's own Text to Image tool: the prompt goes in its prompt_arg, next to its fixed
    args and the values of its own settings (size, quality…) set in the node's details."""
    prompt_arg = str(row.get("prompt_arg") or "prompt")
    fixed = dict(row.get("args") or {})
    pid = str(row.get("plugin_id") or "")
    fields = [dict(field) for field in row.get("config_fields") or [] if isinstance(field, dict) and field.get("id")]
    allowed = {str(field["id"]) for field in fields}

    def args(inputs: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
        settings = ((cfg.get("gateway_config") or {}).get(row["id"]) or {}) if isinstance(cfg.get("gateway_config"), dict) else {}
        chosen = {key: value for key, value in settings.items() if key in allowed and value not in (None, "")} if isinstance(settings, dict) else {}
        return {**fixed, **chosen, prompt_arg: _need_text(inputs, "prompt", "Prompt")}

    out = {"id": row["id"], "label": row["label"], "plugin": _manifest_label(pid) or pid, "plugin_id": pid,
           "tool": row["tool"], "credits": int(row.get("credits") or 0), "args": args}
    if row.get("cost"):
        out["cost"] = str(row["cost"])
    if row.get("paid") is True:
        out["paid"] = True
    if fields:
        out["config_fields"] = fields
    return out


def table(ntype: str) -> list[dict[str, Any]]:
    """The node's backends. Text to Image: every image tool a plugin turned on here
    declares (the account plugin's first, so its gateway is the default pick), plus the
    image node of every AI gateway plugin installed here."""
    if ntype == "image.generate":
        rows = sorted((_declared(row) for row in plugin_image_generators()), key=lambda row: row["plugin_id"] != "account")
        rows.extend(_gateway(pid, node) for node, pid in gateway_image_nodes())
        return rows
    return list(BACKENDS.get(ntype) or [])


# node type → backends, first = default (first that can run, when none is picked).
# Credits are the plugins' own estimates (the amount each tool's spend gate names).
BACKENDS: dict[str, list[dict[str, Any]]] = {
    "image.generate": [],  # what the plugins turned on here declare (table())
    "image.edit": [
        _meshy("meshy_image_to_image", "Meshy · Nano Banana", 5, lambda i, c: {**_image_and_prompt(i, c), "ai_model": "nano-banana"}),
    ],
    "image.remove_bg": [_studio("studio3d_remove_bg", "3D AI Studio", 5, _image, "studio3d")],
    "image.upscale": [_studio("studio3d_image_enhance", "3D AI Studio", 20, _image, "studio3d")],
    "mesh.generate": [
        _meshy("meshy_text_to_3d", "Meshy", 25, _prompt),
        _studio("studio3d_tripo", "Tripo v3", 60, _prompt),
        _studio("studio3d_tencent_rapid", "Hunyuan Rapid", 35, _prompt),
        _studio("studio3d_tencent_pro", "Hunyuan Pro", 80, _prompt),
        _studio("studio3d_tripo_p1", "Tripo P1", 100, _prompt),
    ],
    "mesh.from_image": [
        _meshy("meshy_image_to_3d", "Meshy", 25, _meshy_from_image),
        _studio("studio3d_trellis", "TRELLIS.2", 30, _image),
        _studio("studio3d_tripo", "Tripo v3", 60, _image),
        _studio("studio3d_tencent_rapid", "Hunyuan Rapid", 35, _image),
        _studio("studio3d_tencent_pro", "Hunyuan Pro", 80, _image),
    ],
    "mesh.multi_view": [_meshy("meshy_multi_image_to_3d", "Meshy", 25, _multi_view)],
    "mesh.remesh": [
        _meshy("meshy_remesh", "Meshy remesh", 5, _remesh),
        _studio("studio3d_optimize", "3D AI Studio optimize", 10, _studio_model, "studio3d_optimize"),
    ],
    "mesh.uv_unwrap": [_meshy("meshy_uv_unwrap", "Meshy", 5, lambda i, c: _model_source(i))],
    "mesh.convert": [
        _meshy("meshy_convert", "Meshy", 1, _convert_meshy),
        _studio("studio3d_convert", "3D AI Studio", 10, _convert_studio, "studio3d_convert"),
    ],
    "mesh.render": [_studio("studio3d_render", "3D AI Studio", 15, _studio_model, "studio3d")],
    "mesh.repair": [_studio("studio3d_repair", "3D AI Studio", 75, _studio_model, "studio3d")],
    "mesh.bake": [_studio("studio3d_bake_texture", "3D AI Studio", 5, _bake, "studio3d")],
    "mesh.retexture": [_meshy("meshy_retexture", "Meshy", 10, _retexture)],
    "mesh.rig": [_meshy("meshy_rig", "Meshy", 5, _rig, stage="rig")],
    "mesh.animate": [_meshy("meshy_animate", "Meshy", 3, _animate, stage="animate")],
}
# What each node's main output pin is called and what kind of file it carries.
OUTPUT: dict[str, tuple[str, str]] = {
    "image.generate": ("image", "image"),
    "image.edit": ("image", "image"),
    "image.remove_bg": ("image", "image"),
    "image.upscale": ("image", "image"),
    "mesh.generate": ("mesh", "mesh"),
    "mesh.from_image": ("mesh", "mesh"),
    "mesh.remesh": ("mesh", "mesh"),
    "mesh.retexture": ("mesh", "mesh"),
    "mesh.rig": ("mesh", "mesh"),
    "mesh.animate": ("mesh", "mesh"),
    "mesh.multi_view": ("mesh", "mesh"),
    "mesh.uv_unwrap": ("mesh", "mesh"),
    "mesh.convert": ("mesh", "mesh"),
    "mesh.render": ("image", "image"),
    "mesh.repair": ("mesh", "mesh"),
    "mesh.bake": ("mesh", "mesh"),
}


# --------------------------------------------------------------------------- tools


def tool_fn(name: str) -> Callable[..., Any] | None:
    """The function behind a registered tool, or None when its plugin is off."""
    try:
        from backend.server import mcp

        tool = mcp._tool_manager.get_tool(name)
    except Exception:
        return None
    return getattr(tool, "fn", None) if tool is not None else None


def node_fn(node_type: str) -> Callable[..., Any] | None:
    """A gateway plugin's image node handler, or None when that plugin is off."""
    try:
        from backend.automations.plugin import get_handler

        return get_handler(node_type)
    except Exception:
        return None


def _has_key(provider: str) -> bool:
    try:
        from backend.agent.secrets import get_key

        return bool((get_key(provider) or "").strip())
    except Exception:
        return False


def is_ready(row: dict[str, Any]) -> bool:
    """The plugin is installed, on and loaded (and a gateway has its key)."""
    if row.get("node"):
        return node_fn(row["node"]) is not None and _has_key(str(row.get("key") or ""))
    return tool_fn(row["tool"]) is not None


def _manifest_label(plugin_id: str) -> str:
    try:
        from backend.uefn_plugins.store import load_plugin_manifest

        manifest = load_plugin_manifest(plugin_id) or {} if plugin_id else {}
    except Exception:
        manifest = {}
    return str(manifest.get("label") or manifest.get("name") or "")


def plugin_label(row: dict[str, Any]) -> str:
    """The installed plugin's own name for itself, else the name the table uses."""
    return _manifest_label(str(row.get("plugin_id") or "")) or row["plugin"]


def _missing(row: dict[str, Any]) -> str:
    """What stops this backend: "install", "enable", "key", or "" when nothing known."""
    pid = str(row.get("plugin_id") or "")
    try:
        from backend.uefn_plugins.host import is_plugin_enabled
        from backend.uefn_plugins.store import is_plugin_installed

        if pid and not is_plugin_installed(pid):
            return "install"
        if pid and not is_plugin_enabled(pid):
            return "enable"
    except Exception:
        pass
    if row.get("node") and not _has_key(str(row.get("key") or "")):
        return "key"
    return ""


def why_not(row: dict[str, Any], label: str = "") -> str:
    """What to do so this backend can run: install, turn on, or add the key."""
    name = label or row["plugin"]
    return {
        "install": f"Install the {name} plugin from the Store.",
        "enable": f"Turn on the {name} plugin in Plugins.",
        "key": f"Add your {name} API key in Settings.",
    }.get(_missing(row), f"Turn on the {name} plugin in the Store and add its API key.")


def setup_for(row: dict[str, Any], label: str = "") -> dict[str, str] | None:
    """Where to fix it, for the details' button and its Show me: the plugin's Store page
    (install / turn on) or the gateway's API key in Settings > LLMs."""
    name = label or row["plugin"]
    pid = str(row.get("plugin_id") or "")
    missing = _missing(row)
    if missing in ("install", "enable") and pid:
        verb = "Get" if missing == "install" else "Turn on"
        return {"kind": missing, "route": "settings.store", "item": pid, "label": f"{verb} {name}"}
    if missing == "key":
        return {"kind": "key", "route": "settings.llms", "item": str(row.get("key") or pid),
                "label": f"Add your {name} API key"}
    return None


def cost_text(row: dict[str, Any]) -> str:
    if row.get("cost"):
        return str(row["cost"])  # the plugin's own words ("Ducky AI credit")
    return "Your own API key" if row.get("node") else f"~{row['credits']} credits"


def backends_for(ntype: str) -> list[dict[str, Any]]:
    """The node's backends for its details dropdown: who makes it (the installed plugin's
    name), what it costs, and whether it can run here (if not, why). Image gateway
    controls come from the plugin's direct image handler."""
    out: list[dict[str, Any]] = []
    for row in table(ntype):
        ready = is_ready(row)
        label = plugin_label(row)
        setup = None if ready else setup_for(row, label)
        out.append({
            "id": row["id"], "label": row["label"], "plugin": label, "credits": row["credits"], "cost": cost_text(row),
            "available": ready, **({} if ready else {"reason": why_not(row, label)}),
            **({"setup": setup} if setup else {}),
            **({"config_fields": row["config_fields"], "model": row["model"], "own_key": True} if row.get("node")
               else {"config_fields": row["config_fields"]} if row.get("config_fields") else {}),
            **({"paid": True} if row.get("paid") is True else {}),
        })
    return out


def pick_backend(ntype: str, wanted: Any) -> dict[str, Any]:
    """The picked backend; with none picked, the first one that can run here."""
    rows = table(ntype)
    if ntype == "image.generate" and wanted == "agent":
        raise ValueError("Choose a direct image backend in this node's details. The saved agent backend cannot generate images directly.")
    if not rows:
        if ntype == "image.generate":
            raise ValueError("No direct image backend is installed. Turn on an image-capable gateway or image plugin from the Store.")
        raise ValueError(f"No backends for {ntype}.")
    picked = next((row for row in rows if row["id"] == str(wanted or "")), None)
    if picked is not None:
        return picked
    if ntype == "image.generate" and wanted:
        raise ValueError(f"Image backend {wanted} is unavailable. Choose a direct image backend in this node's details.")
    return next((row for row in rows if is_ready(row)), rows[0])


def _parse(raw: Any) -> dict[str, Any]:
    """A plugin tool's answer: JSON text, or text starting with "Error"."""
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if text.lower().startswith("error"):
        raise RuntimeError(text.split(":", 1)[1].strip() if ":" in text[:12] else text)
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise RuntimeError(text[:300] or "The tool sent nothing back.") from exc
    if not isinstance(data, dict):
        raise RuntimeError("The tool sent back something unexpected.")
    return data


def _main_file(paths: list[str], kind: str) -> str:
    for ext in _PREFERRED.get(kind, ()):
        for path in paths:
            if path.lower().endswith(ext):
                return path
    same = [path for path in paths if kind_of(path) == kind]
    return (same or paths)[0]


def _remote(data: dict[str, Any], kind: str) -> str:
    """The generator's own link to the main file (Meshy edits take it as model_url)."""
    if kind == "mesh":
        urls = data.get("model_urls") if isinstance(data.get("model_urls"), dict) else {}
        for fmt in ("glb", "fbx", "obj"):
            if str(urls.get(fmt) or "").startswith("https://"):
                return str(urls[fmt])
    for item in data.get("results") or []:
        url = item.get("asset") if isinstance(item, dict) else None
        if isinstance(url, str) and url.startswith("https://") and kind_of(url.split("?")[0], "") == kind:
            return url
    return ""


def run_media(ntype: str, cfg: dict[str, Any], inputs: dict[str, Any], folder: Path) -> dict[str, Any]:
    """Run one image / 3D node on the backend picked in its details. With "Try the
    next backend if it fails" on, a failure moves on to the next one that is set up."""
    first = pick_backend(ntype, cfg.get("backend"))
    if ntype == "mesh.animate":
        return _run_animations(cfg, inputs, folder)
    if cfg.get("fallback") is not True:
        return _run_once(ntype, first, cfg, inputs, folder)
    order = [first] + [row for row in table(ntype) if row["id"] != first["id"]]
    errors: list[str] = []
    for backend in order:
        if backend is not first and not is_ready(backend):
            continue
        step = _run_once(ntype, backend, cfg, inputs, folder)
        if step.get("ok"):
            if errors:
                step["result"] = {**step.get("result", {}), "fell_back_after": errors}
            return step
        if step.get("gate"):  # spend switch off or nothing wired: another backend won't help
            return step
        errors.append(str(step.get("error") or "failed"))
    return {"ok": False, "error": "Every backend failed: " + " · ".join(errors)}


def _run_animations(cfg: dict[str, Any], inputs: dict[str, Any], folder: Path) -> dict[str, Any]:
    try:
        actions = action_ids(inputs, cfg)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "gate": True}
    backend = pick_backend("mesh.animate", "")
    meshes: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    for n, action in enumerate(actions):
        step = _run_once("mesh.animate", backend, cfg, {**inputs, "actions": [action]}, folder / f"{n + 1}-{action}", credits_for=len(actions))
        if not step.get("ok"):
            return step if not meshes else {**step, "error": f"Animation {action}: {step.get('error')}"}
        meshes.append(step["outputs"]["mesh"])
        files.extend(step["outputs"]["files"])
    return {"ok": True, "outputs": {"mesh": meshes[0], "meshes": meshes, "files": files},
            "result": {"backend": backend["label"], "animations": actions, "credits": int(backend["credits"]) * len(actions)}}


def _run_gateway(ntype: str, backend: dict[str, Any], cfg: dict[str, Any], inputs: dict[str, Any], folder: Path) -> dict[str, Any]:
    """Run a gateway plugin's own image node and keep the picture with this run."""
    handler = node_fn(backend["node"])
    if handler is None or not _has_key(str(backend.get("key") or "")):
        return {"ok": False, "error": f"{backend['label']}: {why_not(backend, plugin_label(backend))}"}
    try:
        args = backend["args"](inputs, cfg)
    except (ValueError, OSError) as exc:
        return {"ok": False, "gate": True, "error": str(exc)}
    try:
        settings = (cfg.get("gateway_config") or {}).get(backend["id"], {})
        allowed = {field["id"] for field in backend["config_fields"]}
        settings = {key: value for key, value in settings.items() if key in allowed and value not in (None, "")}
        data = handler({"config": {"model": backend["model"], **settings, "prompt": args["prompt"]}, "payload": {}})
    except Exception as exc:  # noqa: BLE001 - a plugin's failure is this node's error
        return {"ok": False, "error": f"{backend['label']}: {exc}"}
    if not isinstance(data, dict) or not data.get("ok"):
        reason = data.get("error") if isinstance(data, dict) else ""
        return {"ok": False, "error": f"{backend['label']} failed: {reason or 'no picture came back'}"}
    made = str(data.get("path") or next((f.get("path") for f in data.get("files") or [] if isinstance(f, dict)), "") or "")
    if not made or not Path(made).is_file():
        return {"ok": False, "error": f"{backend['label']} finished but sent no picture back."}
    folder.mkdir(parents=True, exist_ok=True)
    kept = str(shutil.copy2(made, folder / Path(made).name))
    pin, kind = OUTPUT[ntype]
    ref = {**file_ref(kept, kind), "provider": backend["plugin"], "backend": backend["id"]}
    return {
        "ok": True,
        "outputs": {pin: with_url(ref), "files": [with_url(file_ref(kept, kind))]},
        "result": {"backend": backend["label"], "credits": 0, "billed_to": f"{backend['plugin']} API key", "files": [kept]},
    }


def _run_once(ntype: str, backend: dict[str, Any], cfg: dict[str, Any], inputs: dict[str, Any], folder: Path, *, credits_for: int = 1) -> dict[str, Any]:
    credits = int(backend.get("credits") or 0)
    if backend.get("node"):
        if cfg.get("spend") is not True:
            return {"ok": False, "gate": True, "error": f"{backend['label']} is billed to your {backend['plugin']} API key. Turn on Spend credits in this node's details to let it run."}
        return _run_gateway(ntype, backend, cfg, inputs, folder)
    paid = credits > 0 or backend.get("paid") is True
    if paid and cfg.get("spend") is not True:
        price = backend.get("cost") or f"about {credits * credits_for} credits"
        return {"ok": False, "gate": True, "error": f"{backend['label']} costs {price} a run. Turn on Spend credits in this node's details to let it run."}
    fn = tool_fn(backend["tool"])
    if fn is None:
        return {"ok": False, "error": f"{backend['label']} needs the {backend['plugin']} plugin: turn it on in the Store and add its API key."}
    try:
        args = backend["args"](inputs, cfg)
    except (ValueError, OSError) as exc:
        return {"ok": False, "gate": True, "error": str(exc)}
    args.update({"wait": True, "output_dir": str(folder), **({"confirm_spend": True} if paid else {})})
    try:
        data = _parse(fn(**args))
    except RuntimeError as exc:
        return {"ok": False, "error": f"{backend['label']}: {exc}"}
    if data.get("failed") or str(data.get("status") or "").upper() in ("FAILED", "CANCELED", "CANCELLED"):
        reason = data.get("failure_reason") or data.get("error") or data.get("status")
        return {"ok": False, "error": f"{backend['label']} failed: {reason}"}
    paths = [str(p) for p in data.get("downloaded") or [] if p]
    pin, kind = OUTPUT[ntype]
    if not paths:
        return {"ok": False, "error": f"{backend['label']} finished but sent no files back."}
    wanted = str(cfg.get("format") or "").lower() if ntype == "mesh.convert" else ""
    main = next((path for path in paths if wanted and path.lower().endswith("." + wanted)), "") or _main_file(paths, kind)
    ref: dict[str, Any] = {**file_ref(main, kind), "provider": backend["plugin"], "backend": backend["id"]}
    if data.get("task_id"):
        ref["task_id"] = str(data["task_id"])
    if backend.get("stage"):
        ref["stage"] = backend["stage"]
    remote = _remote(data, kind)
    if remote:
        ref["remote"] = remote
    files = [with_url({**file_ref(path, kind_of(path)), **({"task_id": ref.get("task_id")} if ref.get("task_id") else {})}) for path in paths]
    return {
        "ok": True,
        "outputs": {pin: with_url(ref), "files": files},
        "result": {"backend": backend["label"], "credits": credits, **({"cost": backend["cost"]} if backend.get("cost") else {}),
                   "files": [f["path"] for f in files]},
    }


# --------------------------------------------------------------------------- send on


def send_to_uefn(cfg: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    """Import files into the open UEFN project's Content Browser (needs UEFN running).
    A list (every animation, every picture) imports each one."""
    raw = inputs.get("file")
    items = [item for item in (raw if isinstance(raw, list) else [raw]) if path_of(item)]
    if not items:
        return {"ok": False, "error": "Nothing in File: wire a model, picture or sound in."}
    fn = tool_fn("import_asset")
    if fn is None:
        return {"ok": False, "error": "UEFN tools are off: turn on the UEFN plugin."}
    folder = str(inputs.get("folder") or cfg.get("folder") or "").strip() or "Ducky"
    assets: list[str] = []
    for item in items[:50]:
        path = path_of(item)
        if path.lower().startswith(("http://", "https://", "data:")):
            return {"ok": False, "error": "Send to UEFN takes files on this PC; wire a generator's file or pick one."}
        if not Path(path).is_file():
            return {"ok": False, "error": f"{Path(path).name} isn't on this PC any more."}
        try:
            data = _parse(fn(source_file=path, destination_path=folder, replace_existing=cfg.get("replace") is not False))
        except RuntimeError as exc:
            return {"ok": False, "error": f"UEFN didn't take {Path(path).name}: {exc}"}
        if data.get("ok") is False or data.get("error"):
            return {"ok": False, "error": f"UEFN didn't take {Path(path).name}: {data.get('error') or data}"}
        body = data.get("result") if isinstance(data.get("result"), dict) else data
        imported = [str(entry) for entry in body.get("imported") or [] if entry]
        if "imported" in body and not imported:
            return {"ok": False, "error": f"UEFN imported nothing from {Path(path).name}; check its format."}
        where = str(body.get("destination_path") or folder)
        assets.extend(imported or [f"{where}/{Path(path).stem}"])
    return {"ok": True, "outputs": {"asset": assets[0], "assets": assets}, "result": {"imported": assets, "folder": folder}}


_IMPORT_CODE = """
import bpy
SRC = {src!r}
ext = SRC.lower().rsplit(".", 1)[-1]
old = set(bpy.data.objects)
if ext in ("glb", "gltf"):
    bpy.ops.import_scene.gltf(filepath=SRC)
elif ext == "fbx":
    bpy.ops.import_scene.fbx(filepath=SRC)
elif ext == "obj":
    bpy.ops.wm.obj_import(filepath=SRC)
elif ext == "stl":
    bpy.ops.wm.stl_import(filepath=SRC)
else:
    raise ValueError("Blender opens GLB, FBX, OBJ or STL here")
result = {{"objects": [o.name for o in bpy.data.objects if o not in old]}}
"""


def open_in_blender(_cfg: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    """Import a model into the Blender that is open (Blender plugin + its MCP add-on)."""
    try:
        path = _need_file(inputs, "mesh", "3D model")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        result = _blender(_IMPORT_CODE.format(src=path))
    except RuntimeError as exc:
        return {"ok": False, "error": f"Blender: {exc}"}
    return {"ok": True, "outputs": {"mesh": inputs.get("mesh")}, "result": result}


_RENDER_CODE = """
import bpy, math
from mathutils import Vector
SRC = {src!r}
OUT = {out!r}
W, H = {width}, {height}
before_scene = bpy.context.window_manager.windows[0].scene if bpy.context.window_manager.windows else bpy.context.scene
scene = bpy.data.scenes.new("Ducky render")
win = bpy.context.window_manager.windows[0] if bpy.context.window_manager.windows else None
if win:
    win.scene = scene
old = set(bpy.data.objects)
ext = SRC.lower().rsplit(".", 1)[-1]
if ext in ("glb", "gltf"):
    bpy.ops.import_scene.gltf(filepath=SRC)
elif ext == "fbx":
    bpy.ops.import_scene.fbx(filepath=SRC)
elif ext == "obj":
    bpy.ops.wm.obj_import(filepath=SRC)
else:
    raise ValueError("Blender render takes GLB, FBX or OBJ")
made = [o for o in bpy.data.objects if o not in old]
for o in made:
    if o.name not in scene.collection.all_objects:
        scene.collection.objects.link(o)
pts = [o.matrix_world @ Vector(c) for o in made if o.type == "MESH" for c in o.bound_box]
lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
center, radius = (lo + hi) / 2, max((hi - lo).length / 2, 0.01)
cam = bpy.data.objects.new("Ducky camera", bpy.data.cameras.new("Ducky camera"))
scene.collection.objects.link(cam)
cam.location = center + Vector((1.0, -1.4, 0.8)).normalized() * radius * 3.2
cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
scene.camera = cam
sun = bpy.data.objects.new("Ducky light", bpy.data.lights.new("Ducky light", "SUN"))
sun.rotation_euler = (math.radians(50), 0, math.radians(30))
scene.collection.objects.link(sun)
world = bpy.data.worlds.new("Ducky world")
world.color = (0.05, 0.05, 0.05)
scene.world = world
for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES"):
    try:
        scene.render.engine = engine
        break
    except TypeError:
        continue
scene.render.resolution_x, scene.render.resolution_y = W, H
scene.render.film_transparent = {transparent}
scene.render.image_settings.file_format = "PNG"
scene.render.filepath = OUT
bpy.ops.render.render(write_still=True, scene=scene.name)
for o in made + [cam, sun]:
    bpy.data.objects.remove(o, do_unlink=True)
if win:
    win.scene = before_scene
bpy.data.scenes.remove(scene)
result = {{"image": OUT, "objects": len(made)}}
"""

_EXPORT_CODE = """
import bpy
OUT = {out!r}
bpy.ops.export_scene.gltf(filepath=OUT, export_format="GLB", use_selection={selected})
result = {{"mesh": OUT}}
"""


def _blender(code: str) -> dict[str, Any]:
    fn = tool_fn("blender_execute_blender_code")
    if fn is None:
        raise RuntimeError("Turn on the Blender plugin and open Blender with its MCP add-on.")
    data = _parse(fn(code=code))
    if data.get("ok") is False or data.get("error"):
        raise RuntimeError(str(data.get("error") or data.get("stderr") or "Blender didn't run it."))
    return data.get("result") if isinstance(data.get("result"), dict) else {}


def blender_render(cfg: dict[str, Any], inputs: dict[str, Any], folder: Path) -> dict[str, Any]:
    """Render a model in the running Blender (its own temporary scene) to a PNG."""
    try:
        path = _need_file(inputs, "mesh", "3D model")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    folder.mkdir(parents=True, exist_ok=True)
    out = str(folder / "render.png")
    size = int(_number(inputs, cfg, "size", 1024))
    code = _RENDER_CODE.format(src=path, out=out, width=size, height=size, transparent=cfg.get("transparent") is not False)
    try:
        _blender(code)
    except RuntimeError as exc:
        return {"ok": False, "error": f"Blender: {exc}"}
    if not Path(out).is_file():
        return {"ok": False, "error": "Blender finished but wrote no picture."}
    return {"ok": True, "outputs": {"image": with_url(file_ref(out, "image"))}}


def blender_export(cfg: dict[str, Any], _inputs: dict[str, Any], folder: Path) -> dict[str, Any]:
    """Save what is open in Blender (or just the selection) as a GLB for other 3D nodes."""
    folder.mkdir(parents=True, exist_ok=True)
    out = str(folder / "scene.glb")
    try:
        _blender(_EXPORT_CODE.format(out=out, selected=cfg.get("selected") is True))
    except RuntimeError as exc:
        return {"ok": False, "error": f"Blender: {exc}"}
    if not Path(out).is_file():
        return {"ok": False, "error": "Blender finished but wrote no GLB."}
    return {"ok": True, "outputs": {"mesh": with_url(file_ref(out, "mesh"))}}


def refs_with_urls(value: Any, depth: int = 0) -> Any:
    """Every file ref inside a value gets a fresh link (run outputs, saved picks)."""
    if depth > 4:
        return value
    if is_file_ref(value):
        return with_url(value)
    if isinstance(value, list):
        return [refs_with_urls(item, depth + 1) for item in value]
    if isinstance(value, dict):
        return {key: refs_with_urls(item, depth + 1) for key, item in value.items()}
    return value


def refresh_links(wf: dict[str, Any]) -> dict[str, Any]:
    """Links for every file a workflow shows: picked files and what its runs made."""
    for node in (wf.get("graph") or {}).get("nodes") or []:
        cfg = node.get("config") if isinstance(node, dict) else None
        if isinstance(cfg, dict):
            for key in ("value", "inputs"):
                if key in cfg:
                    cfg[key] = refs_with_urls(cfg[key])
    for run in wf.get("runs") or []:
        if isinstance(run, dict) and isinstance(run.get("node_outputs"), dict):
            run["node_outputs"] = refs_with_urls(run["node_outputs"])
    return wf
