"""Widget tree, slots, animations, and Verse fields.

Uses UMGToolSet / WidgetAnimationToolset / VerseFieldsToolset / MVVMToolset.
Never dumps toolset JSON schemas. Never patches .uasset bytes.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import unreal

from listener.dispatch import register
from listener.registry.umg_spec import (
    BINDING_MODES,
    button_event_name,
    verse_field_spec,
    validate_widget_tree_spec,
)

_UMG = "UMGToolSet.UMGToolSet"
_VERSE_FIELDS = "VerseFieldsToolset.VerseFieldsToolset"

# Short names agents pass. Resolved to unreal classes AddWidget accepted live.
# Text / TextBlock resolve to the palette UEFN_TextBlock_C (umg.add_widget places it).
_CLASS_ALIASES = {
    "CustomButton": "UIFrameworkCustomButtonWidget",
}

_NO_TEXT = (
    "AddWidget rejects CommonTextBlock, RichTextBlock, UIFrameworkTextBlock, and "
    "VerseFortniteUIFrameworkTextBlock. Use class 'Text' (palette UEFN_TextBlock_C); "
    "set its label with properties.text."
)
_NO_PRESET_BUTTON = (
    "AddWidget rejects VerseFortniteUIFrameworkButton_Loud, _Quiet, and _Regular. "
    "The palette button is CustomButton (UIFrameworkCustomButtonWidget)."
)
_REJECTED_WIDGET_CLASSES = {
    "CommonTextBlock": _NO_TEXT,
    "RichTextBlock": _NO_TEXT,
    "UIFrameworkTextBlock": _NO_TEXT,
    "VerseFortniteUIFrameworkTextBlock": _NO_TEXT,
    "LoudButton": _NO_PRESET_BUTTON,
    "QuietButton": _NO_PRESET_BUTTON,
    "RegularButton": _NO_PRESET_BUTTON,
    "VerseFortniteUIFrameworkButton_Loud": _NO_PRESET_BUTTON,
    "VerseFortniteUIFrameworkButton_Quiet": _NO_PRESET_BUTTON,
    "VerseFortniteUIFrameworkButton_Regular": _NO_PRESET_BUTTON,
}

# Pre-42.30 AddVerseField took flat fieldType strings (no events).
_LEGACY_FIELD_TYPES = ("bool", "int", "float", "string", "message", "color", "color_alpha", "texture", "material")

_TRACKS = {
    "Opacity": ("MovieSceneFloatTrack", "RenderOpacity", "RenderOpacity"),
    "Color": ("MovieSceneColorTrack", "ColorAndOpacity", "ColorAndOpacity"),
    "Transform": ("MovieScene2DTransformTrack", "RenderTransform", "RenderTransform"),
}

_COLOR_CHANNELS = ("R", "G", "B", "A")
_TRANSFORM_CHANNELS = (
    "TranslationX",
    "TranslationY",
    "ScaleX",
    "ScaleY",
    "ShearX",
    "ShearY",
    "Angle",
)


def _mod():
    from listener.registry import umg as umg_mod

    return umg_mod


def _load_wbp(widget_path: str):
    return _mod()._load_asset(widget_path)


def _ref(obj: Any) -> str:
    return _mod()._ref_path(obj)


def _execute(toolset: str, tool: str, payload: dict) -> dict:
    return _mod()._execute_tool(toolset, tool, payload)


def _compile(wbp) -> None:
    _mod()._compile_and_save(wbp)


def _err_head(text: str) -> str:
    cut = text.find("Function schema")
    if cut > 0:
        text = text[:cut]
    return text.strip()[:400]


def _load_object(ref_path: str):
    ref_path = (ref_path or "").strip()
    if not ref_path:
        raise ValueError("empty object ref")
    obj = None
    find = getattr(unreal, "find_object", None)
    load = getattr(unreal, "load_object", None)
    if callable(find):
        try:
            obj = find(None, ref_path)
        except Exception:
            obj = None
    if obj is None and callable(load):
        try:
            obj = load(None, ref_path)
        except Exception:
            obj = None
    if obj is None:
        raise ValueError(f"Could not load object {ref_path}")
    return obj


def _class_ref(widget_class: str) -> str:
    name = (widget_class or "").strip()
    if not name:
        raise ValueError("widget class is required")
    if name.startswith("/"):
        return name
    if name in _mod().TEXT_ALIASES:
        return _mod().UEFN_TEXT_BLOCK
    rejected = _REJECTED_WIDGET_CLASSES.get(name)
    if rejected:
        raise ValueError(rejected)
    name = _CLASS_ALIASES.get(name, name)
    cls = getattr(unreal, name, None)
    if cls is None:
        raise ValueError(
            f"unreal.{name} is not in this UEFN build. Call list_widget_classes."
        )
    try:
        return str(cls.static_class().get_path_name())
    except Exception:
        return f"/Script/UMG.{name}"


def _widgets(wbp) -> List[dict]:
    result = _execute(_UMG, "GetWidgets", {"widgetBlueprint": {"refPath": _ref(wbp)}})
    payload = result.get("result") or {}
    if isinstance(payload, dict) and "widgets" in payload:
        return list(payload.get("widgets") or [])
    if isinstance(payload, dict) and isinstance(payload.get("returnValue"), dict):
        return list(payload["returnValue"].get("widgets") or [])
    return []


def _find_widget(rows: List[dict], name: str) -> Optional[dict]:
    for row in rows:
        if str(row.get("widgetName") or "") == name:
            return row
    return None


def _root_row(rows: List[dict]) -> Optional[dict]:
    for row in rows:
        if not row.get("parent"):
            return row
    return rows[0] if rows else None


def _ref_of(node: Any) -> str:
    if isinstance(node, dict):
        return str(node.get("refPath") or "")
    return ""


def list_widget_classes() -> dict:
    """Palette classes from UMGToolSet.ListWidgetClasses (no schema dump)."""
    result = _execute(_UMG, "ListWidgetClasses", {})
    payload = result.get("result")
    rows = payload if isinstance(payload, list) else []
    if isinstance(payload, dict):
        rows = payload.get("returnValue") or payload.get("widgets") or []
    compact = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ref = _ref_of(row.get("widgetClass"))
        compact.append(
            {
                "class": ref.rsplit(".", 1)[-1],
                "ref": ref,
                "panel": bool(row.get("bIsPanel")),
                "category": row.get("category") or "",
            }
        )
    return {"classes": compact, "count": len(compact)}


def get_widget_class_info(widget_class: str) -> dict:
    """One class: panel flag, category, description. Slot fields come from a live slot read."""
    ref = _class_ref(widget_class)
    result = _execute(_UMG, "GetWidgetClassInfo", {"widgetClass": {"refPath": ref}})
    return {"widget_class": widget_class, "ref": ref, "info": result.get("result")}


def _vec2(pair, default=(0.0, 0.0)):
    if not isinstance(pair, (list, tuple)) or len(pair) < 2:
        pair = default
    return unreal.Vector2D(float(pair[0]), float(pair[1]))


def _margin(raw: Any):
    raw = raw or {}
    if not isinstance(raw, dict):
        raw = {}
    margin = unreal.Margin()
    for key, prop in (
        ("left", "left"),
        ("top", "top"),
        ("right", "right"),
        ("bottom", "bottom"),
    ):
        try:
            margin.set_editor_property(prop, float(raw.get(key, 0.0)))
        except Exception:
            pass
    return margin


def _apply_slot(slot_obj, slot: dict) -> List[str]:
    """Write canvas/box/grid slot fields. Returns warnings for keys that did not stick."""
    notes: List[str] = []
    if not isinstance(slot, dict) or not slot:
        return notes
    if "z_order" in slot:
        try:
            slot_obj.set_editor_property("z_order", int(slot["z_order"]))
        except Exception as exc:  # noqa: BLE001
            notes.append(f"z_order: {exc}"[:160])
    if "auto_size" in slot:
        try:
            slot_obj.set_editor_property("bAutoSize", bool(slot["auto_size"]))
        except Exception as exc:  # noqa: BLE001
            notes.append(f"auto_size: {exc}"[:160])
    if any(k in slot for k in ("anchors", "offsets", "alignment")):
        try:
            anchors = unreal.Anchors()
            spec_a = slot.get("anchors") or {}
            anchors.set_editor_property("minimum", _vec2(spec_a.get("min"), (0.0, 0.0)))
            anchors.set_editor_property("maximum", _vec2(spec_a.get("max"), (0.0, 0.0)))
            data = unreal.AnchorData()
            data.set_editor_property("anchors", anchors)
            data.set_editor_property("offsets", _margin(slot.get("offsets")))
            data.set_editor_property("alignment", _vec2(slot.get("alignment"), (0.0, 0.0)))
            slot_obj.set_editor_property("layout_data", data)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"layout_data: {exc}"[:200])
    if "padding" in slot:
        try:
            slot_obj.set_editor_property("padding", _margin(slot.get("padding")))
        except Exception as exc:  # noqa: BLE001
            notes.append(f"padding: {exc}"[:160])
    for key, prop in (
        ("row", "row"),
        ("column", "column"),
        ("row_span", "row_span"),
        ("column_span", "column_span"),
    ):
        if key in slot:
            try:
                slot_obj.set_editor_property(prop, int(slot[key]))
            except Exception as exc:  # noqa: BLE001
                notes.append(f"{key}: {exc}"[:120])
    return notes


def _enum_member(enum_name: str, token: str):
    enum_cls = getattr(unreal, enum_name, None)
    if enum_cls is None:
        return None
    token = str(token or "").strip()
    for candidate in (token, token.upper(), token.upper().replace(" ", "_")):
        member = getattr(enum_cls, candidate, None)
        if member is not None:
            return member
    return None


def _apply_widget_props(widget_obj, props: dict) -> List[str]:
    notes: List[str] = []
    if not isinstance(props, dict) or not props:
        return notes
    if "text" in props:
        try:
            widget_obj.set_editor_property("text", str(props["text"]))
        except Exception as exc:  # noqa: BLE001
            notes.append(f"text: {exc}"[:160])
    if "render_opacity" in props:
        try:
            widget_obj.set_editor_property("render_opacity", float(props["render_opacity"]))
        except Exception as exc:  # noqa: BLE001
            notes.append(f"render_opacity: {exc}"[:160])
    if "visibility" in props:
        member = _enum_member("SlateVisibility", str(props["visibility"]))
        try:
            if member is None:
                raise ValueError(f"unknown visibility {props['visibility']}")
            widget_obj.set_editor_property("visibility", member)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"visibility: {exc}"[:160])
    if "color" in props:
        try:
            rgba = props["color"]
            color = unreal.LinearColor(float(rgba[0]), float(rgba[1]), float(rgba[2]), float(rgba[3]))
            slate = unreal.SlateColor()
            slate.set_editor_property("specified_color", color)
            widget_obj.set_editor_property("color_and_opacity", slate)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"color: {exc}"[:160])
    if "brush_material" in props:
        try:
            mat = unreal.EditorAssetLibrary.load_asset(str(props["brush_material"]))
            setter = getattr(widget_obj, "set_brush_from_material", None)
            if callable(setter):
                setter(mat)
            else:
                raise ValueError("widget has no set_brush_from_material")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"brush_material: {exc}"[:200])
    if "brush_texture" in props:
        try:
            tex = unreal.EditorAssetLibrary.load_asset(str(props["brush_texture"]))
            setter = getattr(widget_obj, "set_brush_from_texture", None)
            if callable(setter):
                setter(tex, False)
            else:
                raise ValueError("widget has no set_brush_from_texture")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"brush_texture: {exc}"[:200])
    return notes


def _add_child(wbp, widget_class: str, widget_name: str, parent_ref: str) -> dict:
    payload = {
        "widgetBlueprint": {"refPath": _ref(wbp)},
        "widgetClass": {"refPath": _class_ref(widget_class)},
        "widgetDisplayName": widget_name,
        "parentWidget": {"refPath": parent_ref} if parent_ref else None,
        "childIndex": -1,
    }
    return _mod().add_widget(payload)


def _place_node(wbp, spec: dict, parent_ref: str) -> dict:
    name = str(spec["name"])
    rows = _widgets(wbp)
    existing = _find_widget(rows, name)
    if existing is None:
        _add_child(wbp, str(spec["class"]), name, parent_ref)
        rows = _widgets(wbp)
        existing = _find_widget(rows, name)
    if existing is None:
        raise ValueError(f"AddWidget did not produce {name}")
    widget_ref = _ref_of(existing.get("widget"))
    slot_ref = _ref_of(existing.get("slot"))
    notes: List[str] = []
    if widget_ref and spec.get("properties"):
        notes.extend(_apply_widget_props(_load_object(widget_ref), spec["properties"]))
    if slot_ref and spec.get("slot"):
        notes.extend(_apply_slot(_load_object(slot_ref), spec["slot"]))
    placed = {
        "name": existing.get("widgetName") or name,
        "class": str(spec["class"]),
        "widget_ref": widget_ref,
        "slot_ref": slot_ref,
        "notes": notes,
        "children": [],
    }
    for child in spec.get("children") or []:
        placed["children"].append(_place_node(wbp, child, widget_ref))
    return placed


def build_widget_tree(
    tree: dict,
    widget_path: str = "",
    asset_name: str = "",
    folder: str = "",
) -> dict:
    """Create or fill a Widget Blueprint from a nested ``{class, name, slot, properties, children}`` spec.

    Compiles and saves once at the end. An existing root canvas is reused when the
    spec root is also a canvas, so a factory-created canvas is not duplicated.
    """
    if not isinstance(tree, dict):
        raise ValueError("tree must be an object {class, name, children}")
    validate_widget_tree_spec(tree)
    created = False
    if widget_path and unreal.EditorAssetLibrary.does_asset_exist(widget_path):
        wbp = _load_wbp(widget_path)
    else:
        if not asset_name:
            raise ValueError("asset_name is required when widget_path does not exist")
        created_info = _mod().create_widget_blueprint(asset_name, folder or "")
        widget_path = created_info["widget_path"]
        wbp = _load_wbp(widget_path)
        created = True

    rows = _widgets(wbp)
    root = _root_row(rows)
    root_class = str(tree.get("class") or "")
    if root is not None and root_class in ("CanvasPanel", "canvas", "Canvas"):
        parent_ref = _ref_of(root.get("widget"))
        placed_children = []
        for child in tree.get("children") or []:
            placed_children.append(_place_node(wbp, child, parent_ref))
        placed = {
            "name": root.get("widgetName"),
            "class": "CanvasPanel",
            "widget_ref": parent_ref,
            "reused_root": True,
            "children": placed_children,
        }
        if tree.get("slot") or tree.get("properties"):
            pass
    else:
        parent_ref = _ref_of(root.get("widget")) if root is not None else ""
        placed = _place_node(wbp, tree, parent_ref)

    _compile(wbp)
    return {
        "widget_path": _ref(wbp),
        "created": created,
        "tree": placed,
    }


def set_widget_slot(widget_path: str, widget_name: str, slot: dict) -> dict:
    """Set one child's slot (anchors, offsets, alignment, ZOrder, padding, grid row/column)."""
    if not isinstance(slot, dict) or not slot:
        raise ValueError("slot must be a non-empty object")
    wbp = _load_wbp(widget_path)
    row = _find_widget(_widgets(wbp), widget_name)
    if row is None:
        raise ValueError(f"No widget named {widget_name}")
    slot_ref = _ref_of(row.get("slot"))
    if not slot_ref:
        raise ValueError(f"{widget_name} has no slot (it is the root)")
    notes = _apply_slot(_load_object(slot_ref), slot)
    _compile(wbp)
    return {
        "widget_path": _ref(wbp),
        "widget_name": widget_name,
        "slot_ref": slot_ref,
        "notes": notes,
        "slot": _read_slot(slot_ref),
    }


def _num(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_vec(obj) -> Optional[List[float]]:
    if obj is None:
        return None
    x = _num(getattr(obj, "x", None))
    y = _num(getattr(obj, "y", None))
    if x is None or y is None:
        try:
            x = _num(obj.get_editor_property("x"))
            y = _num(obj.get_editor_property("y"))
        except Exception:
            return None
    if x is None or y is None:
        return None
    return [x, y]


def _read_margin(obj) -> Optional[dict]:
    if obj is None:
        return None
    out = {}
    for key in ("left", "top", "right", "bottom"):
        val = getattr(obj, key, None)
        if val is None:
            try:
                val = obj.get_editor_property(key)
            except Exception:
                val = None
        num = _num(val)
        if num is not None:
            out[key] = num
    return out or None


def _read_slot(slot_ref: str) -> dict:
    if not slot_ref:
        return {}
    try:
        obj = _load_object(slot_ref)
    except Exception as exc:  # noqa: BLE001
        return {"ref": slot_ref, "error": str(exc)[:160]}
    out: Dict[str, Any] = {"ref": slot_ref}
    try:
        out["class"] = obj.get_class().get_name()
    except Exception:
        pass
    try:
        out["z_order"] = int(obj.get_editor_property("z_order"))
    except Exception:
        pass
    try:
        data = obj.get_editor_property("layout_data")
        anchors = data.get_editor_property("anchors") if hasattr(data, "get_editor_property") else None
        if anchors is not None:
            out["anchors"] = {
                "min": _read_vec(anchors.get_editor_property("minimum")),
                "max": _read_vec(anchors.get_editor_property("maximum")),
            }
        out["offsets"] = _read_margin(data.get_editor_property("offsets"))
        out["alignment"] = _read_vec(data.get_editor_property("alignment"))
    except Exception:
        pass
    return out


def list_named_slots(widget_path: str) -> dict:
    wbp = _load_wbp(widget_path)
    result = _execute(_UMG, "GetNamedSlots", {"widgetBlueprint": {"refPath": _ref(wbp)}})
    return {"widget_path": _ref(wbp), "slots": result.get("result")}


def set_named_slot_content(
    widget_path: str,
    slot_name: str,
    content_class: str = "",
    content_widget_name: str = "",
) -> dict:
    """Place a new class or an existing widget into a NamedSlot."""
    if not slot_name:
        raise ValueError("slot_name is required")
    wbp = _load_wbp(widget_path)
    payload: Dict[str, Any] = {
        "widgetBlueprint": {"refPath": _ref(wbp)},
        "slotName": slot_name,
    }
    if content_class:
        payload["widgetClass"] = {"refPath": _class_ref(content_class)}
    if content_widget_name:
        row = _find_widget(_widgets(wbp), content_widget_name)
        if row is None:
            raise ValueError(f"No widget named {content_widget_name}")
        payload["contentWidget"] = {"refPath": _ref_of(row.get("widget"))}
    result = _execute(_UMG, "SetNamedSlotContent", payload)
    _compile(wbp)
    return {"widget_path": _ref(wbp), "slot_name": slot_name, "result": result.get("result")}


def _anim_info_dict(info) -> dict:
    if info is None:
        return {}
    out = {}
    for key in ("animation_name", "display_label", "length_seconds", "binding_count"):
        try:
            val = info.get_editor_property(key)
        except Exception:
            val = getattr(info, key, None)
        if val is not None:
            out[key] = val if not hasattr(val, "get_name") else str(val)
            if key == "animation_name":
                out[key] = str(val)
    anim = None
    try:
        anim = info.get_editor_property("animation")
    except Exception:
        anim = getattr(info, "animation", None)
    out["has_animation"] = anim is not None
    return out


def _animation_object(wbp, animation_name: str):
    tool = unreal.WidgetAnimationToolset
    info = tool.find_widget_animation(wbp, unreal.Name(animation_name))
    anim = None
    try:
        anim = info.get_editor_property("animation")
    except Exception:
        anim = getattr(info, "animation", None)
    if anim is None:
        raise ValueError(f"Animation not found: {animation_name}")
    return anim, info


def create_widget_animation(widget_path: str, animation_name: str, length_seconds: float = 1.0) -> dict:
    wbp = _load_wbp(widget_path)
    info = unreal.WidgetAnimationToolset.create_widget_animation(
        wbp, unreal.Name(animation_name), float(length_seconds)
    )
    summary = _anim_info_dict(info)
    if not summary.get("has_animation"):
        raise ValueError(f"create_widget_animation failed for {animation_name}")
    _compile(wbp)
    return {"widget_path": _ref(wbp), "animation": summary}


def _widget_object(wbp, widget_name: str):
    row = _find_widget(_widgets(wbp), widget_name)
    if row is None:
        raise ValueError(f"No widget named {widget_name}")
    return _load_object(_ref_of(row.get("widget"))), row


def bind_widget_animation(widget_path: str, animation_name: str, widget_name: str) -> dict:
    wbp = _load_wbp(widget_path)
    anim, _info = _animation_object(wbp, animation_name)
    widget_obj, _row = _widget_object(wbp, widget_name)
    proxy = unreal.WidgetAnimationToolset.add_widget_to_animation(wbp, anim, widget_obj)
    guid = ""
    try:
        guid = str(proxy.get_id())
    except Exception:
        guid = ""
    _compile(wbp)
    return {
        "widget_path": _ref(wbp),
        "animation_name": animation_name,
        "widget_name": widget_name,
        "binding_id": guid,
    }


def _channel_for(section, track_type: str, channel: str):
    want = (channel or "").strip()
    channels = list(section.get_all_channels() or [])
    if track_type == "Opacity":
        if not channels:
            raise ValueError("opacity track has no channels")
        return channels[0], "Opacity"
    names = _COLOR_CHANNELS if track_type == "Color" else _TRANSFORM_CHANNELS
    if want not in names:
        raise ValueError(f"channel must be one of {list(names)}")
    index = names.index(want)
    if index >= len(channels):
        raise ValueError(f"track has {len(channels)} channels, cannot address {want}")
    return channels[index], want


def add_animation_keys(
    widget_path: str,
    animation_name: str,
    widget_name: str,
    track_type: str,
    keys: list,
    channel: str = "",
) -> dict:
    """Add a property track if needed and key it.

    ``track_type`` is Opacity, Color, or Transform.
    ``keys`` is ``[{time, value}]`` in seconds. Color/Transform require ``channel``.
    """
    if track_type not in _TRACKS:
        raise ValueError(f"track_type must be one of {list(_TRACKS)}")
    if not isinstance(keys, list) or len(keys) < 1:
        raise ValueError("keys must be a non-empty list of {time, value}")
    track_cls_name, prop_name, prop_path = _TRACKS[track_type]
    track_cls = getattr(unreal, track_cls_name, None)
    if track_cls is None:
        raise ValueError(f"unreal.{track_cls_name} is not in this UEFN build")

    wbp = _load_wbp(widget_path)
    anim, _info = _animation_object(wbp, animation_name)
    widget_obj, _row = _widget_object(wbp, widget_name)
    proxy = unreal.WidgetAnimationToolset.add_widget_to_animation(wbp, anim, widget_obj)
    track = None
    for existing in list(proxy.get_tracks() or []):
        try:
            if existing.get_class().get_name() == track_cls_name:
                track = existing
                break
        except Exception:
            continue
        try:
            if prop_name and str(existing.get_property_name()) == prop_name:
                track = existing
                break
        except Exception:
            continue
    if track is None:
        track = proxy.add_track(track_cls)
        track.set_property_name_and_path(unreal.Name(prop_name), prop_path)
    sections = list(track.get_sections() or [])
    section = sections[0] if sections else track.add_section()
    channel_obj, channel_name = _channel_for(section, track_type, channel)
    written = []
    for key in keys:
        if not isinstance(key, dict):
            raise ValueError("each key must be {time, value}")
        seconds = float(key["time"])
        value = float(key["value"])
        frame = unreal.FrameNumber(int(round(seconds * 30.0)))
        channel_obj.add_key(
            frame,
            value,
            time_unit=unreal.MovieSceneTimeUnit.DISPLAY_RATE,
        )
        written.append({"time": seconds, "value": value, "channel": channel_name})
    _compile(wbp)
    return {
        "widget_path": _ref(wbp),
        "animation_name": animation_name,
        "widget_name": widget_name,
        "track_type": track_type,
        "keys": written,
        "key_count": _key_count(channel_obj),
    }


def _key_count(channel) -> int:
    try:
        return int(channel.get_num_keys())
    except Exception:
        return -1


def list_widget_animations(widget_path: str) -> dict:
    wbp = _load_wbp(widget_path)
    infos = unreal.WidgetAnimationToolset.list_widget_animations(wbp)
    rows = []
    for info in list(infos or []):
        summary = _anim_info_dict(info)
        anim = None
        try:
            anim = info.get_editor_property("animation")
        except Exception:
            anim = getattr(info, "animation", None)
        tracks = []
        if anim is not None:
            try:
                proxies = unreal.WidgetAnimationToolset.get_widget_animation_bindings(anim)
            except Exception:
                proxies = []
            for proxy in list(proxies or []):
                for track in list(proxy.get_tracks() or []):
                    key_count = 0
                    try:
                        for section in list(track.get_sections() or []):
                            for ch in list(section.get_all_channels() or []):
                                key_count += int(ch.get_num_keys() or 0)
                    except Exception:
                        pass
                    try:
                        tname = track.get_class().get_name()
                    except Exception:
                        tname = type(track).__name__
                    tracks.append({"track": tname, "key_count": key_count})
        summary["tracks"] = tracks
        rows.append(summary)
    return {"widget_path": _ref(wbp), "animations": rows}


def _compile_checked(wbp) -> dict:
    """UMGToolSet compile (reports binding errors), then save. Never raises on a compile error."""
    try:
        _execute(_UMG, "CompileWidgetBlueprint", {"widgetBlueprint": {"refPath": _ref(wbp)}})
        compiled, error = True, ""
    except ValueError as exc:
        compiled, error = False, _err_head(str(exc))
    try:
        unreal.EditorAssetLibrary.save_loaded_asset(wbp, only_if_is_dirty=False)
    except Exception:  # noqa: BLE001
        pass
    return {"compiled": compiled, **({"compile_error": error} if error else {})}


def _field_names(widget_path: str) -> List[str]:
    rows = list_verse_fields(widget_path).get("fields") or []
    return [str(row.get("name") or row.get("fieldName") or "") for row in rows if isinstance(row, dict)]


# 42.30: a widget's Verse fields only become real once its asset editor has been opened in this
# editor session. Until then the event field is a transient object, so the Assets digest lists the
# widget with no members (Verse: E3506 "Unknown member") and a click binding is saved with no target
# (after a reload: "Event '…' => Self.<None>()': The event could not be generated"). Opening and
# closing the editor once fixes both — verified live in UEFN 42.30, Oct 2 2026. Remembered per
# listener session; creating a widget at a path forgets it.
_VERSE_FIELDS_READY: set = set()


def _package(path: str) -> str:
    return (path or "").split(".")[0]


def _open_asset_packages() -> set:
    try:
        payload = _execute("EditorToolset.EditorAppToolset", "GetOpenAssets", {}).get("result")
    except ValueError:
        return set()
    if isinstance(payload, dict):
        payload = payload.get("returnValue")
    return {_package(str(p)) for p in payload or []}


def ensure_verse_fields_live(wbp) -> str:
    """Open the widget's asset editor once (then close it) so its Verse fields are real.

    Returns "opened", "already_open", "ready" (done earlier this session) or "failed: …".
    """
    key = _package(_ref(wbp))
    if key in _VERSE_FIELDS_READY:
        return "ready"
    if key in _open_asset_packages():
        _VERSE_FIELDS_READY.add(key)
        return "already_open"
    try:
        editors = unreal.get_editor_subsystem(unreal.AssetEditorSubsystem)
        editors.open_editor_for_assets([wbp])
        editors.close_all_editors_for_asset(wbp)
    except Exception as exc:  # noqa: BLE001
        return f"failed: {exc}"[:200]
    _VERSE_FIELDS_READY.add(key)
    return "opened"


def forget_verse_fields_live(asset_path: str) -> None:
    """A widget was (re)created at this path: open its editor again before the next field change."""
    _VERSE_FIELDS_READY.discard(_package(asset_path))


def add_verse_field(
    widget_path: str,
    field_name: str,
    field_type: str,
    default_value: str = "",
    event_parameters: Optional[List[str]] = None,
    mutable: bool = True,
    visibility: str = "public",
) -> dict:
    """Add a Verse field via VerseFieldsToolset.AddVerseField (42.30 `spec`; flat payload before 42.30)."""
    if not field_name or not field_type:
        raise ValueError("field_name and field_type are required")
    spec = verse_field_spec(field_type, default_value, event_parameters, mutable, visibility)
    wbp = _load_wbp(widget_path)
    live = ensure_verse_fields_live(wbp)
    bp = {"refPath": _ref(wbp)}
    try:
        result = _execute(_VERSE_FIELDS, "AddVerseField", {"widgetBlueprint": bp, "fieldName": field_name, "spec": spec})
    except ValueError as exc:
        # Before 42.30 the tool wanted flat fieldType/defaultValue/visibility/bIsVar and had no events.
        if "fieldType" not in str(exc):
            raise
        if spec["type"] not in _LEGACY_FIELD_TYPES:
            raise ValueError("This UEFN build cannot create event fields (UEFN 42.30+ can).") from exc
        result = _execute(_VERSE_FIELDS, "AddVerseField", {
            "widgetBlueprint": bp, "fieldName": field_name, "fieldType": spec["type"],
            "defaultValue": spec["defaultValue"], "visibility": spec["visibility"], "bIsVar": spec["bIsVar"],
        })
    compiled = _compile_checked(wbp)
    return {
        "widget_path": _ref(wbp),
        "field_name": field_name,
        "field_type": spec["type"],
        "event_parameters": spec["eventParameterTypes"],
        "result": result.get("result"),
        "listed_names": _field_names(widget_path),
        "verse_ready": live,
        **compiled,
    }


def edit_verse_field(
    widget_path: str,
    field_name: str,
    field_type: str = "",
    default_value: Optional[str] = None,
    event_parameters: Optional[List[str]] = None,
    mutable: Optional[bool] = None,
    visibility: str = "",
    new_name: str = "",
) -> dict:
    """Retype / re-default / rename a field (EditVerseField, 42.30). Unset arguments keep their value."""
    wbp = _load_wbp(widget_path)
    live = ensure_verse_fields_live(wbp)
    rows = list_verse_fields(widget_path).get("fields") or []
    current = next((r for r in rows if isinstance(r, dict) and (r.get("name") or r.get("fieldName")) == field_name), None)
    if current is None:
        raise ValueError(f"No Verse field named {field_name}. Fields: {_field_names(widget_path)}")
    kind = field_type or str(current.get("type") or "")
    spec = verse_field_spec(
        kind,
        default_value if default_value is not None else str(current.get("defaultValue") or ""),
        event_parameters if event_parameters is not None else (list(current.get("eventParameterTypes") or []) if kind == "event" else None),
        mutable if mutable is not None else bool(current.get("bIsVar", True)),
        visibility or str(current.get("visibility") or "public"),
    )
    result = _execute(_VERSE_FIELDS, "EditVerseField", {
        "widgetBlueprint": {"refPath": _ref(wbp)}, "fieldName": field_name, "spec": spec, "newName": new_name or "",
    })
    return {"widget_path": _ref(wbp), "field_name": new_name or field_name, "spec": spec,
            "result": result.get("result"), "listed_names": _field_names(widget_path), "verse_ready": live,
            **_compile_checked(wbp)}


def remove_verse_field(widget_path: str, field_name: str) -> dict:
    wbp = _load_wbp(widget_path)
    ensure_verse_fields_live(wbp)
    result = _execute(_VERSE_FIELDS, "RemoveVerseField", {"widgetBlueprint": {"refPath": _ref(wbp)}, "fieldName": field_name})
    return {"widget_path": _ref(wbp), "removed": field_name, "result": result.get("result"),
            "listed_names": _field_names(widget_path), **_compile_checked(wbp)}


def duplicate_verse_field(widget_path: str, field_name: str, new_name: str) -> dict:
    if not new_name:
        raise ValueError("new_name is required and must not be in use")
    wbp = _load_wbp(widget_path)
    ensure_verse_fields_live(wbp)
    result = _execute(_VERSE_FIELDS, "DuplicateVerseField", {
        "widgetBlueprint": {"refPath": _ref(wbp)}, "fieldName": field_name, "newName": new_name,
    })
    return {"widget_path": _ref(wbp), "field_name": new_name, "result": result.get("result"),
            "listed_names": _field_names(widget_path), **_compile_checked(wbp)}


def list_verse_field_types() -> dict:
    """Field and event-parameter types this UEFN build accepts (GetSupportedVerseFieldTypes)."""
    result = _execute(_VERSE_FIELDS, "GetSupportedVerseFieldTypes", {})
    payload = result.get("result")
    if isinstance(payload, dict):
        payload = payload.get("returnValue", payload)
    return {"types": payload}


def list_verse_fields(widget_path: str) -> dict:
    wbp = _load_wbp(widget_path)
    result = _execute(
        _VERSE_FIELDS,
        "ListVerseFields",
        {"widgetBlueprint": {"refPath": _ref(wbp)}},
    )
    payload = result.get("result")
    fields = payload if isinstance(payload, list) else []
    if isinstance(payload, dict):
        fields = payload.get("returnValue") or payload.get("fields") or []
    return {"widget_path": _ref(wbp), "fields": fields}


def _mvvm():
    cls = getattr(unreal, "MVVMToolset", None)
    if cls is None:
        raise ValueError("MVVMToolset is not in this UEFN build")
    return cls


def bind_verse_field(
    widget_path: str,
    source_field: str,
    widget_name: str,
    destination_property: str,
    conversion_name: str = "",
    mode: str = "OneWayToDestination",
) -> dict:
    """Drive a child widget property from a Verse field (BindWidgetPropertyToVerseField, 42.30)."""
    if mode not in BINDING_MODES:
        raise ValueError(f"mode must be one of {list(BINDING_MODES)}")
    wbp = _load_wbp(widget_path)
    live = ensure_verse_fields_live(wbp)
    widget_obj, _row = _widget_object(wbp, widget_name)
    try:
        result = _execute(_VERSE_FIELDS, "BindWidgetPropertyToVerseField", {
            "widgetBlueprint": {"refPath": _ref(wbp)},
            "verseFieldName": source_field,
            "targetWidget": {"refPath": _ref(widget_obj)},
            "widgetPropertyPath": destination_property,
            "mode": mode,
            "conversionName": conversion_name or "None",
        })
        payload = result.get("result")
        binding_id = payload.get("returnValue") if isinstance(payload, dict) else payload
    except ValueError as exc:
        # Before 42.30: MVVM view binding (one way only).
        if "BindWidgetPropertyToVerseField" not in str(exc) and "execute_tool" not in str(exc):
            raise
        binding_id = _mvvm().create_view_binding(wbp, None, source_field, widget_obj, destination_property, conversion_name or "")
    return {
        "widget_path": _ref(wbp),
        "source_field": source_field,
        "widget_name": widget_name,
        "destination_property": destination_property,
        "mode": mode,
        "binding_id": str(binding_id),
        "verse_ready": live,
        **_compile_checked(wbp),
    }


def bind_widget_event(
    widget_path: str,
    widget_name: str,
    event_name: str,
    destination_field: str,
) -> dict:
    """Bind a widget event to a Verse field: an `event` field (42.30) or a bool/int the device watches.

    Custom Buttons compile only OnButtonClicked / OnButtonHighlight / OnButtonUnhighlight in 42.30;
    OnClicked, OnPressed, OnHovered… are remapped. The compile result is returned, not hidden.
    The widget's editor is opened once first (ensure_verse_fields_live): without that the binding
    points at a transient object and is saved with no target.
    """
    wbp = _load_wbp(widget_path)
    live = ensure_verse_fields_live(wbp)
    widget_obj, row = _widget_object(wbp, widget_name)
    widget_class = _ref_of(row.get("widgetClassPath")) if isinstance(row, dict) else ""
    used = button_event_name(event_name, widget_class)
    if destination_field not in _field_names(widget_path):
        raise ValueError(
            f"No Verse field named {destination_field}. Add it first: add_verse_field(field_type='event') "
            f"for a click, or bool/int. Fields: {_field_names(widget_path)}"
        )
    event = _mvvm().create_view_event_binding(wbp, widget_obj, used, None, destination_field)
    return {
        "widget_path": _ref(wbp),
        "widget_name": widget_name,
        "event_name": used,
        **({"event_name_requested": event_name} if used != event_name else {}),
        "destination_field": destination_field,
        "event": str(event)[:200],
        "verse_ready": live,
        **_compile_checked(wbp),
    }


def list_bindable_properties(widget_path: str, widget_name: str) -> dict:
    wbp = _load_wbp(widget_path)
    widget_obj, _row = _widget_object(wbp, widget_name)
    names = list(_mvvm().list_bindable_widget_properties(wbp, widget_obj) or [])
    return {"widget_path": _ref(wbp), "widget_name": widget_name, "properties": [str(n) for n in names]}


def _view_binding_summary(wbp) -> dict:
    cls = getattr(unreal, "MVVMToolset", None)
    if cls is None:
        return {}
    rows = list(cls.list_widget_view_bindings(wbp) or [])
    events = [str(item) for item in list(cls.list_widget_view_events(wbp) or [])]
    modes = []
    for row in rows:
        try:
            modes.append(str(row.get_editor_property("binding_type")))
        except Exception:
            modes.append("binding")
    return {
        "binding_count": len(rows),
        "binding_modes": modes,
        "bindable_events": events[:40],
    }


def enrich_widget_info(wbp, info: dict) -> None:
    """Attach slot anchors/ZOrder and animation key counts onto a blueprint info dict."""
    tree = info.get("tree")
    rows: List[dict] = []
    if isinstance(tree, dict):
        rows = list(tree.get("widgets") or [])
        if not rows and isinstance(tree.get("returnValue"), dict):
            rows = list(tree["returnValue"].get("widgets") or [])
    slots = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        slot_ref = _ref_of(row.get("slot"))
        if not slot_ref:
            continue
        summary = _read_slot(slot_ref)
        summary["widget_name"] = row.get("widgetName")
        slots.append(summary)
    info["slots"] = slots
    try:
        info["animations"] = list_widget_animations(_ref(wbp)).get("animations") or []
    except Exception as exc:  # noqa: BLE001
        info["animations_error"] = str(exc)[:200]
    try:
        info["verse_fields"] = list_verse_fields(_ref(wbp)).get("fields") or []
    except Exception as exc:  # noqa: BLE001
        info["verse_fields_error"] = str(exc)[:200]
    try:
        info["view_bindings"] = _view_binding_summary(wbp)
    except Exception as exc:  # noqa: BLE001
        info["view_bindings_error"] = str(exc)[:200]


register("list_widget_classes")(list_widget_classes)
register("get_widget_class_info")(get_widget_class_info)
register("build_widget_tree")(build_widget_tree)
register("set_widget_slot")(set_widget_slot)
register("list_named_slots")(list_named_slots)
register("set_named_slot_content")(set_named_slot_content)
register("create_widget_animation")(create_widget_animation)
register("bind_widget_animation")(bind_widget_animation)
register("add_animation_keys")(add_animation_keys)
register("list_widget_animations")(list_widget_animations)
register("add_verse_field")(add_verse_field)
register("edit_verse_field")(edit_verse_field)
register("remove_verse_field")(remove_verse_field)
register("duplicate_verse_field")(duplicate_verse_field)
register("list_verse_field_types")(list_verse_field_types)
register("list_verse_fields")(list_verse_fields)
register("bind_verse_field")(bind_verse_field)
register("bind_widget_event")(bind_widget_event)
register("list_bindable_properties")(list_bindable_properties)
