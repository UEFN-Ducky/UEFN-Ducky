"""Asset pipeline registry tools: import/export, dependencies, folders, saving."""

from __future__ import annotations

import os
from typing import Any, Optional

import unreal

from listener.asset_resolve import follow_redirector, load_asset_resolved
from listener.dispatch import register


def _asset_registry():
    return unreal.AssetRegistryHelpers.get_asset_registry()


def _package_name(asset_path: str) -> str:
    # Strip an "Object'Pkg.Object'" wrapper or ".Object" suffix to a package name.
    p = asset_path
    if "." in p and p.rsplit("/", 1)[-1].count(".") >= 1:
        p = p.split(".")[0]
    return p


def _asset_class_name(data: unreal.AssetData) -> str:
    if hasattr(data, "asset_class_path"):
        return str(data.asset_class_path.asset_name)
    return str(getattr(data, "asset_class", ""))


def _static_mesh_metadata(mesh: unreal.StaticMesh, asset_path: str) -> dict[str, Any]:
    info: dict[str, Any] = {
        "asset_path": asset_path,
        "name": mesh.get_name(),
        "path": mesh.get_path_name(),
        "asset_class": "StaticMesh",
    }
    try:
        sub = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
        if sub is not None:
            info["lod_count"] = int(sub.get_lod_count(mesh))
    except Exception:
        pass
    try:
        if hasattr(mesh, "get_editor_property"):
            nanite = mesh.get_editor_property("nanite_settings")
            enabled = False
            if nanite is not None:
                try:
                    enabled = bool(nanite.get_editor_property("enabled"))
                except Exception:
                    enabled = bool(nanite)
            info["has_nanite"] = enabled
            if enabled:
                info["preview_note"] = (
                    "Nanite mesh — preview uses fallback geometry; Open in UEFN for full detail."
                )
    except Exception:
        pass
    try:
        mats = mesh.get_editor_property("static_materials")
        info["material_slots"] = len(mats) if mats is not None else 0
    except Exception:
        pass
    try:
        body_setup = mesh.get_editor_property("body_setup")
        if body_setup is not None:
            info["collision_trace_flag"] = str(body_setup.get_editor_property("collision_trace_flag"))
    except Exception:
        pass
    return info


def _find_exported_fbx(output_directory: str, stem: str) -> Optional[str]:
    """Return the best FBX path written under ``output_directory`` for ``stem``."""
    preferred = os.path.join(output_directory, f"{stem}.fbx")
    if os.path.isfile(preferred):
        return preferred
    preferred_upper = os.path.join(output_directory, f"{stem}.FBX")
    if os.path.isfile(preferred_upper):
        return preferred_upper
    try:
        candidates = [
            os.path.join(output_directory, name)
            for name in os.listdir(output_directory)
            if name.lower().endswith(".fbx")
        ]
    except OSError:
        return None
    if not candidates:
        return None
    stem_l = stem.lower()
    named = [p for p in candidates if stem_l in os.path.basename(p).lower()]
    pool = named or candidates
    pool.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return pool[0]


def _make_fbx_export_options():
    """Build preview-friendly FBX options when the UEFN build exposes them."""
    cls = getattr(unreal, "FbxExportOption", None)
    if cls is None:
        return None
    try:
        options = cls()
    except Exception:
        return None
    for prop, value in (
        ("collision", False),
        ("level_of_detail", False),
        ("vertex_color", True),
        ("ascii", False),
    ):
        try:
            if hasattr(options, prop):
                options.set_editor_property(prop, value)
        except Exception:
            try:
                setattr(options, prop, value)
            except Exception:
                pass
    return options


def _export_static_mesh_fbx(mesh: unreal.StaticMesh, asset_path: str, output_path: str) -> str:
    """Non-interactive StaticMesh → FBX export. Does not modify the project asset."""
    out_dir = os.path.dirname(output_path)
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(output_path))[0] or mesh.get_name() or "mesh"

    exporter = None
    for cls_name in ("StaticMeshExporterFBX", "ExporterFBX"):
        cls = getattr(unreal, cls_name, None)
        if cls is None:
            continue
        try:
            exporter = cls()
            break
        except Exception:
            exporter = None

    task = unreal.AssetExportTask()
    task.object = mesh
    task.filename = output_path
    task.selected = False
    task.replace_identical = True
    task.prompt = False
    task.automated = True
    if exporter is not None:
        task.exporter = exporter
    options = _make_fbx_export_options()
    if options is not None:
        try:
            task.options = options
        except Exception:
            try:
                task.set_editor_property("options", options)
            except Exception:
                pass

    ok = False
    try:
        ok = bool(unreal.Exporter.run_asset_export_task(task))
    except Exception:
        ok = False
    if not ok:
        try:
            unreal.AssetToolsHelpers.get_asset_tools().export_asset_tasks([task])
            ok = True
        except Exception:
            ok = False
    if not ok:
        # Last resort: directory export (still non-interactive when automated tasks fail).
        unreal.AssetToolsHelpers.get_asset_tools().export_assets([asset_path], out_dir)

    found = _find_exported_fbx(out_dir, stem)
    if found is None:
        raise RuntimeError(
            f"Static mesh export produced no FBX for {asset_path} (may be protected or unsupported)."
        )
    if found != output_path and not os.path.isfile(output_path):
        try:
            os.replace(found, output_path)
            found = output_path
        except OSError:
            pass
    return found

def create_folder(path: str) -> dict:
    """Create a content folder under the project mount (rewrites /Game/... when needed)."""
    from listener.project_paths import normalize_project_folder

    path = normalize_project_folder(path, default_subpath="")
    ok = unreal.EditorAssetLibrary.make_directory(path)
    return {"path": path, "created": bool(ok)}


def import_asset(source_file: str, destination_path: str, replace_existing: bool = True) -> dict:
    """Import a file (fbx/png/wav/...) into a content path."""
    from listener.project_paths import normalize_project_folder

    if not os.path.isfile(source_file):
        raise ValueError(f"Source file not found: {source_file}")
    destination_path = normalize_project_folder(destination_path, default_subpath="Imported")
    task = unreal.AssetImportTask()
    task.filename = source_file
    task.destination_path = destination_path
    task.replace_existing = bool(replace_existing)
    task.automated = True
    task.save = True
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    asset_tools.import_asset_tasks([task])
    imported = [str(p) for p in (task.imported_object_paths or [])]
    return {"source_file": source_file, "destination_path": destination_path, "imported": imported, "count": len(imported)}


def export_asset(asset_path: str, output_directory: str) -> dict:
    """Export an asset to a directory on disk."""
    asset = unreal.EditorAssetLibrary.load_asset(asset_path)
    if asset is None:
        raise ValueError(f"Asset not found: {asset_path}")
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    asset_tools.export_assets([asset_path], output_directory)
    return {"asset_path": asset_path, "output_directory": output_directory}


def preview_static_mesh(asset_path: str, output_directory: str, filename: str = "model.fbx") -> dict:
    """Export a StaticMesh to a caller-owned cache directory for Ducky's 3D viewer.

    Read-only: does not save, spawn, or mutate project assets. Writes only under
    ``output_directory`` (AppData cache), never under Content/.
    """
    path = (asset_path or "").strip()
    out_dir = (output_directory or "").strip()
    name = (filename or "model.fbx").strip() or "model.fbx"
    if not path:
        raise ValueError("asset_path is required")
    if not out_dir:
        raise ValueError("output_directory is required")
    if "/" in name or "\\" in name or name in {".", ".."}:
        raise ValueError("filename must be a bare file name")
    if not name.lower().endswith(".fbx"):
        name = f"{name}.fbx"

    data = unreal.EditorAssetLibrary.find_asset_data(path)
    asset_class = ""
    if data is not None:
        try:
            valid = data.is_valid() if callable(getattr(data, "is_valid", None)) else True
        except Exception:
            valid = True
        if valid:
            asset_class = _asset_class_name(data)
    mesh, resolved_path = load_asset_resolved(path)
    if mesh is None:
        raise ValueError(f"Asset not found: {path}")
    # load_asset can still hand back a redirector on some builds; follow again.
    mesh = follow_redirector(mesh)
    if mesh is None:
        raise ValueError(f"Asset not found: {path}")
    if not isinstance(mesh, unreal.StaticMesh):
        cls = type(mesh).__name__ or asset_class or "Unknown"
        raise ValueError(f"Not a StaticMesh (got {cls}). Only static meshes can be previewed.")

    export_path = resolved_path or path
    os.makedirs(out_dir, exist_ok=True)
    output_path = os.path.join(out_dir, name)
    exported = _export_static_mesh_fbx(mesh, export_path, output_path)
    meta = _static_mesh_metadata(mesh, export_path)
    siblings = []
    try:
        for fn in os.listdir(out_dir):
            full = os.path.join(out_dir, fn)
            if os.path.isfile(full):
                siblings.append(fn)
    except OSError:
        siblings = [os.path.basename(exported)]

    result = {
        "ok": True,
        "asset_path": export_path,
        "asset_class": "StaticMesh",
        "exported_file": exported,
        "output_directory": out_dir,
        "filename": os.path.basename(exported),
        "siblings": siblings,
        "metadata": meta,
    }
    if export_path != path:
        result["requested_path"] = path
        result["followed_redirector"] = True
    return result

def get_dependencies(asset_path: str) -> dict:
    """List package dependencies of an asset (what it references)."""
    pkg = _package_name(asset_path)
    deps = _asset_registry().get_dependencies(pkg, unreal.AssetRegistryDependencyOptions())
    names = [str(d) for d in (deps or [])]
    return {"asset_path": asset_path, "dependencies": names, "count": len(names)}


def get_referencers(asset_path: str) -> dict:
    """List packages that reference an asset (what depends on it)."""
    pkg = _package_name(asset_path)
    refs = _asset_registry().get_referencers(pkg, unreal.AssetRegistryDependencyOptions())
    names = [str(r) for r in (refs or [])]
    return {"asset_path": asset_path, "referencers": names, "count": len(names)}


def _text_list(values) -> list[str]:
    import re

    out: list[str] = []
    for value in values or []:
        try:
            text = str(value)
        except Exception:
            text = repr(value)
        text = text.strip()
        if not text:
            continue
        # UEFN validator Text often arrives as LOCGEN_FORMAT / INVTEXT blobs.
        inv = re.findall(r'INVTEXT\("((?:\\.|[^"\\])*)"\)', text)
        if inv:
            # Prefer the human sentence over the validator class name token.
            candidates = [s.encode("utf-8").decode("unicode_escape") for s in inv]
            preferred = [s for s in candidates if " " in s or "/" in s]
            text = preferred[-1] if preferred else candidates[0]
        out.append(text)
    return out


def _validation_result_name(result) -> str:
    name = getattr(result, "name", None)
    if isinstance(name, str) and name:
        return name
    return str(result)


def validate_uefn_asset(asset_path: str, usecase: str = "MANUAL") -> dict:
    """Run UEFN's registered asset validators (publish/cook blockers) on one asset.

    Prefer this over inferring blockers from ``get_dependencies`` alone — a
    ``/Script/NiagaraEditor`` dependency is supporting evidence, not proof of
    Custom HLSL or other disallowed graph content.
    """
    path = (asset_path or "").strip()
    if not path:
        raise ValueError("asset_path is required")
    if not unreal.EditorAssetLibrary.does_asset_exist(path):
        raise ValueError(f"Asset not found: {path}")

    sub_cls = getattr(unreal, "EditorValidatorSubsystem", None)
    if sub_cls is None:
        raise ValueError("EditorValidatorSubsystem is not exposed in this UEFN build")
    sub = unreal.get_editor_subsystem(sub_cls)
    if sub is None:
        raise ValueError("EditorValidatorSubsystem is unavailable")

    usecase_enum = getattr(unreal, "DataValidationUsecase", None)
    if usecase_enum is None:
        raise ValueError("DataValidationUsecase is not exposed in this UEFN build")
    usecase_name = (usecase or "MANUAL").strip().upper() or "MANUAL"
    usecase_value = getattr(usecase_enum, usecase_name, None)
    if usecase_value is None:
        available = sorted(
            n for n in dir(usecase_enum) if n and n[0].isupper() and not n.startswith("_")
        )
        raise ValueError(f"Unknown usecase: {usecase!r}. Use one of {available}")

    asset_data = unreal.EditorAssetLibrary.find_asset_data(path)
    if asset_data is None:
        raise ValueError(f"AssetData not found: {path}")

    result, errors, warnings = sub.is_asset_valid(asset_data, usecase_value)
    result_name = _validation_result_name(result)
    valid_enum = getattr(unreal, "DataValidationResult", None)
    valid = bool(valid_enum is not None and result == valid_enum.VALID)
    invalid = bool(valid_enum is not None and result == valid_enum.INVALID)
    error_texts = _text_list(errors)
    warning_texts = _text_list(warnings)
    return {
        "asset_path": path,
        "usecase": usecase_name,
        "result": result_name,
        "valid": valid,
        "invalid": invalid,
        "ok": valid and not error_texts,
        "errors": error_texts,
        "warnings": warning_texts,
        "error_count": len(error_texts),
        "warning_count": len(warning_texts),
        "note": (
            "Authoritative UEFN validator output. get_dependencies is supporting evidence only "
            "(/Script/NiagaraEditor alone does not prove NiagaraNodeCustomHlsl)."
        ),
    }


def fixup_redirectors(directory: str = "/Game/") -> dict:
    """Resolve and remove asset redirectors under a directory."""
    ar = _asset_registry()
    filt = unreal.ARFilter(
        package_paths=[directory],
        recursive_paths=True,
        class_names=["ObjectRedirector"],
    )
    found = ar.get_assets(filt)
    redirectors = []
    for data in found or []:
        obj = unreal.AssetRegistryHelpers.get_asset(data)
        if obj is not None:
            redirectors.append(obj)
    if redirectors:
        unreal.AssetToolsHelpers.get_asset_tools().fixup_referencers(redirectors)
    return {"directory": directory, "fixed": len(redirectors)}


def save_directory(directory: str = "/Game/", only_if_dirty: bool = True) -> dict:
    """Save all assets under a content directory."""
    ok = unreal.EditorAssetLibrary.save_directory(directory, only_if_is_dirty=bool(only_if_dirty), recursive=True)
    return {"directory": directory, "saved": bool(ok)}


register("create_folder")(create_folder)
register("import_asset")(import_asset)
register("export_asset")(export_asset)
register("preview_static_mesh")(preview_static_mesh)
register("get_dependencies")(get_dependencies)
register("get_referencers")(get_referencers)
register("validate_uefn_asset")(validate_uefn_asset)
register("fixup_redirectors")(fixup_redirectors)
register("save_directory")(save_directory)
