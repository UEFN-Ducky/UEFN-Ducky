"""UMG / Widget Blueprint registry tools (capability-guarded).

Composable primitives:

  READ    umg_capabilities, list_widget_blueprints, get_widget_blueprint_info,
          list_widget_bindings
  CREATE  create_widget_blueprint
  CHANGE  add_widget_to_tree, set_widget_property, remove_widget_from_tree,
          add_widget_binding, remove_widget_binding

HARD RULES (UEFN crash avoidance):
  - NEVER call ToolsetRegistry.get_all_toolset_json_schemas() or
    get_toolset_json_schema() — dumping the ~50KB+ UMGToolSet schema through
    Python hard-crashes UnrealEditorFortnite (EXCEPTION_ACCESS_VIOLATION).
  - Tree edits go through ToolsetRegistry.execute_tool with SMALL known tool
    names + tiny JSON payloads only.
  - WidgetTree / RootWidget on WidgetBlueprint are protected — do not read them
    via get_editor_property.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional

import unreal

from listener.dispatch import register
from listener.registry.asset_registry import assets_by_class as _assets_by_class

_UMG_CLASSES = (
    "WidgetBlueprint",
    "WidgetBlueprintFactory",
    "WidgetTree",
    "CanvasPanel",
    "TextBlock",
    "Button",
    "MVVMEditorSubsystem",
    "BlueprintEditorLibrary",
    "ToolsetRegistry",
    "UMGToolSet",
)

_HARD_LIST_CAP = 200

# ToolsetRegistry names are "<Class>.<Class>" (verified live). Short "UMGToolSet"
# alone returns "Toolset not found". NEVER dump get_*_json_schema — crashes UEFN.
_UMG_TOOLSET = "UMGToolSet.UMGToolSet"
_OBJECT_TOOLSET = "ObjectTools.ObjectTools"
_KNOWN_UMG_TOOLS = (
    "GetWidgets",
    "GetWidgetDescription",
    "ListWidgetClasses",
    "GetWidgetClassInfo",
    "AddWidget",
    "RemoveWidget",
    "WrapWidgets",
    "SetNamedSlotContent",
    "GetNamedSlots",
    "ToggleWidgetAsVariable",
    "ReplaceWidgetWithTemplate",
)

_KNOWN_OBJECT_TOOLS = (
    "list_properties",
    "get_properties",
    "set_properties",
)


def _capabilities() -> dict:
    return {name: hasattr(unreal, name) for name in _UMG_CLASSES}


def _require(name: str):
    cls = getattr(unreal, name, None)
    if cls is None:
        raise ValueError(f"unreal.{name} is not exposed in this UEFN build. Capabilities: {_capabilities()}")
    return cls


def _load_asset(path: str):
    asset = unreal.EditorAssetLibrary.load_asset(path)
    if asset is None:
        raise ValueError(f"Asset not found: {path}")
    return asset


def _ref_path(obj_or_path: Any) -> str:
    if isinstance(obj_or_path, str):
        return obj_or_path
    get_path = getattr(obj_or_path, "get_path_name", None)
    if callable(get_path):
        return str(get_path())
    return str(obj_or_path)


def _toolset_registry():
    reg_cls = getattr(unreal, "ToolsetRegistry", None)
    if reg_cls is None:
        return None
    try:
        return reg_cls.get_default_object()
    except Exception:
        return None


def _ensure_toolset_registered(reg, class_name: str) -> None:
    """Best-effort register_toolset_class so execute_tool can resolve the name."""
    cls = getattr(unreal, class_name, None)
    if cls is None:
        return
    try:
        reg.register_toolset_class(cls.static_class())
    except Exception:
        pass


def _execute_tool(toolset_name: str, tool_name: str, payload: dict) -> dict:
    """Call ToolsetRegistry.execute_tool with a small JSON payload. Never dumps schemas."""
    reg = _toolset_registry()
    if reg is None:
        raise ValueError("ToolsetRegistry is not available in this UEFN build.")
    if not reg.is_available():
        raise ValueError("ToolsetRegistry.is_available() is False (editor not ready?).")

    # Class stems: "UMGToolSet.UMGToolSet" → UMGToolSet
    stem = toolset_name.split(".")[0]
    _ensure_toolset_registered(reg, stem)

    # Toolset name must be the dotted form; tool name is the short leaf.
    tool_candidates = [tool_name]
    if "." in tool_name:
        tool_candidates.append(tool_name.rsplit(".", 1)[-1])
    last_err = None
    for name in tool_candidates:
        try:
            result = reg.execute_tool(toolset_name, name, json.dumps(payload))
            return _unwrap_tool_result(result, toolset_name=toolset_name, tool_name=name)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            continue
    raise ValueError(
        f"execute_tool({toolset_name!r}, {tool_name!r}) failed. "
        f"Last error: {last_err}. Known UMG tools: {list(_KNOWN_UMG_TOOLS)}"
    )


def _unwrap_tool_result(result: Any, *, toolset_name: str, tool_name: str) -> dict:
    """Normalize ToolCallAsyncResultString / plain str / dict into a dict."""
    if result is None:
        return {"ok": True, "toolset": toolset_name, "tool": tool_name, "result": None}

    # Prefer dedicated accessors on ToolCallAsyncResultString
    err = ""
    try:
        if hasattr(result, "get_editor_property"):
            err = str(result.get_editor_property("error") or "")
        elif hasattr(result, "error"):
            err = str(result.error or "")
    except Exception:
        err = ""
    if err.strip():
        raise ValueError(f"{toolset_name}.{tool_name} error: {err.strip()}")

    val: Any = None
    get_json = getattr(result, "get_value_as_json_string", None)
    if callable(get_json):
        try:
            val = get_json()
        except Exception:
            val = None
    if val is None or str(val) == "":
        try:
            if hasattr(result, "get_editor_property"):
                val = result.get_editor_property("value")
            else:
                val = getattr(result, "value", None)
        except Exception:
            val = None

    if isinstance(result, (dict, list)) and (val is None or str(val) == ""):
        return {"ok": True, "toolset": toolset_name, "tool": tool_name, "result": result}

    if val is None or str(val) == "":
        return {"ok": True, "toolset": toolset_name, "tool": tool_name, "result": None}
    return _parse_jsonish(val, toolset_name=toolset_name, tool_name=tool_name)


def _parse_jsonish(val: Any, *, toolset_name: str, tool_name: str) -> dict:
    if isinstance(val, (dict, list)):
        parsed: Any = val
    else:
        text = str(val).strip()
        if not text:
            return {"ok": True, "toolset": toolset_name, "tool": tool_name, "result": None}
        parsed = text
        # ToolCallAsyncResultString often returns a JSON-encoded string (quotes included).
        for _ in range(2):
            if not isinstance(parsed, str):
                break
            try:
                parsed = json.loads(parsed)
            except json.JSONDecodeError:
                break
    if isinstance(parsed, dict) and "returnValue" in parsed and len(parsed) == 1:
        parsed = parsed["returnValue"]
    return {"ok": True, "toolset": toolset_name, "tool": tool_name, "result": parsed}


def _compile_and_save(wbp) -> None:
    bel = getattr(unreal, "BlueprintEditorLibrary", None)
    if bel is not None and hasattr(bel, "compile_blueprint"):
        bel.compile_blueprint(wbp)
    unreal.EditorAssetLibrary.save_loaded_asset(wbp, only_if_is_dirty=False)


def _mvvm_subsystem():
    cls = getattr(unreal, "MVVMEditorSubsystem", None)
    if cls is None:
        raise ValueError("MVVMEditorSubsystem is not exposed in this UEFN build.")
    ss = unreal.get_editor_subsystem(cls)
    if ss is None:
        raise ValueError("MVVMEditorSubsystem could not be acquired (editor not ready?).")
    return ss


def umg_capabilities() -> dict:
    """Probe UMG / MVVM / ToolsetRegistry availability. NEVER dumps toolset schemas."""
    classes = _capabilities()
    reg = _toolset_registry()
    toolset = {
        "available": bool(reg and reg.is_available()) if reg else False,
        "umg_toolset_name": _UMG_TOOLSET,
        "object_toolset_name": _OBJECT_TOOLSET,
        "umg_toolset_registered_by_name": False,
        "object_tools_registered_by_name": False,
        "known_umg_tools": list(_KNOWN_UMG_TOOLS),
        "known_object_tools": list(_KNOWN_OBJECT_TOOLS),
        "schema_dump_banned": True,
    }
    if reg is not None:
        _ensure_toolset_registered(reg, "UMGToolSet")
        _ensure_toolset_registered(reg, "ObjectTools")
        try:
            toolset["umg_toolset_registered_by_name"] = bool(reg.is_toolset_registered(_UMG_TOOLSET))
        except Exception as exc:  # noqa: BLE001
            toolset["umg_toolset_class_error"] = str(exc)[:200]
        try:
            toolset["object_tools_registered_by_name"] = bool(reg.is_toolset_registered(_OBJECT_TOOLSET))
        except Exception:
            toolset["object_tools_registered_by_name"] = False

    mvvm_ok = bool(classes.get("MVVMEditorSubsystem"))
    conversion_fns: List[str] = []
    if mvvm_ok:
        try:
            ss = _mvvm_subsystem()
            # get_available_conversion_functions may need a view — leave empty on miss.
            if hasattr(ss, "get_available_conversion_functions"):
                conversion_fns = ["available_via_MVVMEditorSubsystem.get_available_conversion_functions"]
        except Exception as exc:  # noqa: BLE001
            conversion_fns = [f"probe_error:{str(exc)[:120]}"]

    return {
        "classes": classes,
        "toolset": toolset,
        "mvvm": {"available": mvvm_ok, "conversion_functions_note": conversion_fns},
        "notes": [
            "create_widget_blueprint uses WidgetBlueprintFactory (proven in UEFN).",
            "WidgetTree/RootWidget editor properties are PROTECTED — use GetWidgets via UMGToolSet.",
            "NEVER call get_all_toolset_json_schemas / get_toolset_json_schema — dumps crash UEFN (AV).",
            "Tree scaffolding: add_widget_to_tree → open_asset_in_uefn for designer polish.",
            "Verse fields (38.00+) and Verse field events (39.40+) are authored in the UMG Variables panel; "
            "they appear in the Assets digest as the UW_* type members.",
            "Compile with BlueprintEditorLibrary.compile_blueprint after tree edits.",
        ],
    }


def list_widget_blueprints(search: str = "", offset: int = 0, limit: int = 50) -> dict:
    """List WidgetBlueprint assets in the project (filter with ``search``, paged)."""
    _require("WidgetBlueprint")
    limit = max(0, min(int(limit), _HARD_LIST_CAP))
    offset = max(0, int(offset))
    q = (search or "").strip().lower()
    paths: List[str] = []
    for data in _assets_by_class("/Script/UMGEditor", "WidgetBlueprint"):
        try:
            full = f"{data.package_name}.{data.asset_name}"
        except Exception:
            continue
        if q and q not in full.lower():
            continue
        paths.append(full)
    # Also try UMG module path variants some builds use
    if not paths:
        for data in _assets_by_class("/Script/UMG", "WidgetBlueprint"):
            try:
                full = f"{data.package_name}.{data.asset_name}"
            except Exception:
                continue
            if q and q not in full.lower():
                continue
            paths.append(full)
    paths.sort()
    page = paths[offset : offset + limit]
    return {
        "paths": page,
        "count": len(paths),
        "offset": offset,
        "limit": limit,
        "truncated": offset + limit < len(paths),
    }


def get_widget_blueprint_info(widget_path: str) -> dict:
    """Inspect a WidgetBlueprint: member vars, event dispatchers, tree (via UMGToolSet)."""
    wbp = _load_asset(widget_path)
    if not isinstance(wbp, _require("WidgetBlueprint")):
        # Some loads return the generated class fallback — still try path.
        pass
    info: dict = {
        "widget_path": _ref_path(wbp),
        "asset_name": wbp.get_name(),
        "status": str(getattr(wbp, "status", "")),
    }
    try:
        info["parent_class"] = str(wbp.parent_class())
    except Exception as exc:  # noqa: BLE001
        info["parent_class_error"] = str(exc)[:160]
    try:
        info["generated_class"] = str(wbp.generated_class())
    except Exception as exc:  # noqa: BLE001
        info["generated_class_error"] = str(exc)[:160]

    try:
        members = list(wbp.list_member_variable_names())
        # Prefer project-authored names (skip inherited /Script/ paths for readability).
        authored = [m for m in members if not str(m).startswith("/Script/")]
        info["member_variables"] = authored[:200]
        info["member_variables_inherited_count"] = len(members) - len(authored)
    except Exception as exc:  # noqa: BLE001
        info["member_variables_error"] = str(exc)[:200]

    try:
        info["event_dispatchers"] = list(wbp.list_event_dispatchers())
    except Exception as exc:  # noqa: BLE001
        info["event_dispatchers_error"] = str(exc)[:200]

    # Tree via UMGToolSet.GetWidgets — small execute_tool call only.
    if _capabilities().get("ToolsetRegistry") and _capabilities().get("UMGToolSet"):
        try:
            tree = _execute_tool(
                _UMG_TOOLSET,
                "GetWidgets",
                {"widgetBlueprint": {"refPath": _ref_path(wbp)}},
            )
            # GetWidgets returns {returnValue: {info, widgets}}
            info["tree"] = tree.get("result")
            info["tree_source"] = "UMGToolSet.GetWidgets"
        except Exception as exc:  # noqa: BLE001
            info["tree_error"] = str(exc)[:300]
            info["tree_note"] = (
                "GetWidgets unavailable — open the asset in the designer "
                "(open_asset_in_uefn) or retry after umg_capabilities."
            )
    else:
        info["tree_note"] = "UMGToolSet / ToolsetRegistry not available — tree dump skipped."

    # MVVM view bindings (best-effort)
    if _capabilities().get("MVVMEditorSubsystem"):
        try:
            info["bindings"] = list_widget_bindings(widget_path).get("bindings", [])
        except Exception as exc:  # noqa: BLE001
            info["bindings_error"] = str(exc)[:200]

    return info


def create_widget_blueprint(
    asset_name: str,
    folder: str = "/Game/UI",
    parent_class: str = "UserWidget",
) -> dict:
    """Create an empty WidgetBlueprint asset (errors if it already exists)."""
    wbp_cls = _require("WidgetBlueprint")
    factory_cls = _require("WidgetBlueprintFactory")
    unreal.EditorAssetLibrary.make_directory(folder)
    full = f"{folder.rstrip('/')}/{asset_name}"
    if unreal.EditorAssetLibrary.does_asset_exist(full):
        raise ValueError(f"Asset already exists: {full} (delete_asset first to replace)")

    factory = factory_cls()
    # Optional parent class — best-effort; factory defaults to UserWidget.
    if parent_class and parent_class != "UserWidget":
        try:
            parent = getattr(unreal, parent_class, None)
            if parent is not None and hasattr(factory, "set_editor_property"):
                factory.set_editor_property("parent_class", parent)
        except Exception:
            pass

    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    wbp = asset_tools.create_asset(asset_name, folder, wbp_cls, factory)
    if wbp is None:
        raise RuntimeError(f"create_asset returned None for {full}")
    _compile_and_save(wbp)
    return {
        "widget_path": str(wbp.get_path_name()),
        "asset_name": asset_name,
        "folder": folder,
        "parent_class": parent_class,
    }


def add_widget_to_tree(
    widget_path: str,
    widget_class: str,
    widget_name: str,
    parent_ref_path: str = "",
) -> dict:
    """Add a widget under a panel via UMGToolSet.AddWidget (capability-gated).

    ``widget_class`` is an unreal class name (e.g. TextBlock, CanvasPanel, Button)
    or a soft class path. ``parent_ref_path`` is the panel's refPath from
    get_widget_blueprint_info; leave empty to add as / replace root.
    """
    wbp = _load_asset(widget_path)
    class_ref = _resolve_widget_class_ref(widget_class)
    payload: dict = {
        "widgetBlueprint": {"refPath": _ref_path(wbp)},
        "widgetClass": {"refPath": class_ref},
        "widgetDisplayName": widget_name,
        "parentWidget": {"refPath": parent_ref_path} if parent_ref_path else None,
        "childIndex": -1,
    }
    result = _execute_tool(_UMG_TOOLSET, "AddWidget", payload)
    _compile_and_save(wbp)
    return {
        "widget_path": _ref_path(wbp),
        "added": result.get("result"),
        "tool": result.get("tool"),
    }


def remove_widget_from_tree(widget_path: str, widget_ref_path: str) -> dict:
    """Remove a widget instance from the tree via UMGToolSet.RemoveWidget."""
    wbp = _load_asset(widget_path)
    result = _execute_tool(
        _UMG_TOOLSET,
        "RemoveWidget",
        {
            "widgetBlueprint": {"refPath": _ref_path(wbp)},
            "widget": {"refPath": widget_ref_path},
        },
    )
    _compile_and_save(wbp)
    return {"widget_path": _ref_path(wbp), "removed": result.get("result"), "tool": result.get("tool")}


def set_widget_property(
    widget_path: str,
    target_ref_path: str,
    properties: dict,
    list_first: bool = True,
) -> dict:
    """Set properties on a widget or slot via ObjectTools (list → set).

    Property names vary per class — when ``list_first`` is True (default), call
    ObjectTools.list_properties first and include the names in the response so
    agents can correct typos. Never guess property names.
    """
    if not isinstance(properties, dict) or not properties:
        raise ValueError("properties must be a non-empty object of {name: value}")
    wbp = _load_asset(widget_path)  # ensure asset exists / dirty tracking
    listed: Optional[dict] = None
    if list_first:
        try:
            listed = _execute_tool(
                _OBJECT_TOOLSET,
                "list_properties",
                {"object": {"refPath": target_ref_path}},
            )
        except Exception as exc:  # noqa: BLE001
            listed = {"ok": False, "error": str(exc)[:200]}

    result = _execute_tool(
        _OBJECT_TOOLSET,
        "set_properties",
        {
            "object": {"refPath": target_ref_path},
            "properties": properties,
        },
    )
    _compile_and_save(wbp)
    return {
        "widget_path": _ref_path(wbp),
        "target_ref_path": target_ref_path,
        "listed_properties": listed.get("result") if isinstance(listed, dict) else listed,
        "set_result": result.get("result"),
    }


def _resolve_widget_class_ref(widget_class: str) -> str:
    name = (widget_class or "").strip()
    if not name:
        raise ValueError("widget_class is required (e.g. 'TextBlock', 'CanvasPanel')")
    if name.startswith("/"):
        return name
    cls = getattr(unreal, name, None)
    if cls is None:
        raise ValueError(
            f"unreal.{name} not found. Pass a class name exposed on unreal "
            f"(TextBlock, CanvasPanel, Button, Image, …) or a soft class path."
        )
    # Soft path for Class objects
    try:
        return str(cls.static_class().get_path_name())
    except Exception:
        return f"/Script/UMG.{name}"


def list_widget_bindings(widget_path: str) -> dict:
    """List MVVM view bindings on a WidgetBlueprint (best-effort)."""
    wbp = _load_asset(widget_path)
    ss = _mvvm_subsystem()
    bindings_out: List[Any] = []
    view = None
    for meth in ("get_view", "request_view"):
        fn = getattr(ss, meth, None)
        if not callable(fn):
            continue
        try:
            view = fn(wbp)
            if view is not None:
                break
        except Exception:
            continue
    if view is None:
        return {
            "widget_path": _ref_path(wbp),
            "bindings": [],
            "note": "No MVVM view on this widget (get_view/request_view returned None).",
        }

    # Enumerate bindings via common property / method names.
    for attr in ("bindings", "Bindings", "get_bindings"):
        try:
            if hasattr(view, "get_editor_property"):
                try:
                    val = view.get_editor_property(attr)
                    if val is not None:
                        bindings_out = _serialize_bindings(val)
                        break
                except Exception:
                    pass
            val = getattr(view, attr, None)
            if callable(val):
                val = val()
            if val is not None:
                bindings_out = _serialize_bindings(val)
                break
        except Exception:
            continue

    return {"widget_path": _ref_path(wbp), "bindings": bindings_out, "view": _ref_path(view)}


def _serialize_bindings(val: Any) -> List[Any]:
    out: List[Any] = []
    try:
        items = list(val)
    except Exception:
        return [{"repr": repr(val)[:200]}]
    for item in items[:100]:
        try:
            if hasattr(item, "to_dict"):
                out.append(item.to_dict())
            elif hasattr(item, "get_editor_property"):
                row = {}
                for key in ("source", "destination", "binding_mode", "conversion_function"):
                    try:
                        row[key] = str(item.get_editor_property(key))
                    except Exception:
                        pass
                out.append(row or {"repr": repr(item)[:160]})
            else:
                out.append({"repr": repr(item)[:160]})
        except Exception as exc:  # noqa: BLE001
            out.append({"error": str(exc)[:120]})
    return out


def add_widget_binding(
    widget_path: str,
    source_path: str = "",
    destination_path: str = "",
) -> dict:
    """Add an MVVM binding via MVVMEditorSubsystem.add_binding (best-effort).

    Exact path shapes vary by build — call list_widget_bindings / umg_capabilities
    first. When source/destination are empty, this still ensures a view exists and
    returns guidance for finishing the bind in the designer.
    """
    wbp = _load_asset(widget_path)
    ss = _mvvm_subsystem()
    view = None
    for meth in ("request_view", "get_view"):
        fn = getattr(ss, meth, None)
        if callable(fn):
            try:
                view = fn(wbp)
                if view is not None:
                    break
            except Exception:
                continue
    if view is None:
        raise ValueError(
            "Could not get/create an MVVM view on this WidgetBlueprint. "
            "Open the asset and add a Viewmodel in the View Bindings panel, then retry."
        )

    add_fn = getattr(ss, "add_binding", None)
    if not callable(add_fn):
        raise ValueError("MVVMEditorSubsystem.add_binding is not available in this build.")

    if not source_path and not destination_path:
        return {
            "widget_path": _ref_path(wbp),
            "view": _ref_path(view),
            "added": False,
            "note": (
                "View exists. Pass source_path + destination_path once you know the "
                "exact MVVM field paths (or finish the binding in the designer via "
                "open_asset_in_uefn)."
            ),
        }

    # Builds differ on add_binding signature — try common shapes.
    errors: List[str] = []
    for args in (
        (view,),
        (wbp,),
        (view, source_path, destination_path),
        (wbp, source_path, destination_path),
    ):
        try:
            result = add_fn(*args)
            _compile_and_save(wbp)
            return {
                "widget_path": _ref_path(wbp),
                "view": _ref_path(view),
                "added": True,
                "result": repr(result)[:200],
                "source_path": source_path,
                "destination_path": destination_path,
            }
        except TypeError as exc:
            errors.append(str(exc)[:120])
            continue
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc)[:160])
            continue
    raise ValueError(
        "add_binding call failed for this build. "
        f"Tried common signatures. Errors: {errors}. "
        "Finish the binding in the UMG View Bindings panel (open_asset_in_uefn)."
    )


def remove_widget_binding(widget_path: str, binding_index: int = 0) -> dict:
    """Remove an MVVM binding by index via MVVMEditorSubsystem.remove_binding."""
    wbp = _load_asset(widget_path)
    ss = _mvvm_subsystem()
    listed = list_widget_bindings(widget_path)
    bindings = listed.get("bindings") or []
    if not bindings:
        raise ValueError("No bindings to remove on this widget.")
    idx = int(binding_index)
    if idx < 0 or idx >= len(bindings):
        raise ValueError(f"binding_index {idx} out of range (0..{len(bindings) - 1})")

    view = None
    for meth in ("get_view", "request_view"):
        fn = getattr(ss, meth, None)
        if callable(fn):
            try:
                view = fn(wbp)
                if view is not None:
                    break
            except Exception:
                continue
    remove_fn = getattr(ss, "remove_binding", None)
    if not callable(remove_fn):
        raise ValueError("MVVMEditorSubsystem.remove_binding is not available in this build.")

    # Prefer removing by binding object when we can re-fetch the live list.
    errors: List[str] = []
    for args in ((view, idx), (wbp, idx), (view,), (wbp,)):
        try:
            remove_fn(*args)
            _compile_and_save(wbp)
            return {"widget_path": _ref_path(wbp), "removed_index": idx, "ok": True}
        except TypeError as exc:
            errors.append(str(exc)[:120])
            continue
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc)[:160])
            continue
    raise ValueError(f"remove_binding failed. Errors: {errors}")


register("umg_capabilities")(umg_capabilities)
register("list_widget_blueprints")(list_widget_blueprints)
register("get_widget_blueprint_info")(get_widget_blueprint_info)
register("create_widget_blueprint")(create_widget_blueprint)
register("add_widget_to_tree")(add_widget_to_tree)
register("remove_widget_from_tree")(remove_widget_from_tree)
register("set_widget_property")(set_widget_property)
register("list_widget_bindings")(list_widget_bindings)
register("add_widget_binding")(add_widget_binding)
register("remove_widget_binding")(remove_widget_binding)
