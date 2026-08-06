"""Asset pipeline registry tools: import/export, dependencies, folders, saving."""

from __future__ import annotations

import os
from typing import Any, Optional

import unreal

from listener.dispatch import register
from listener.project_paths import content_root, pin_project_folder


def _asset_registry():
    return unreal.AssetRegistryHelpers.get_asset_registry()


def _package_name(asset_path: str) -> str:
    # Strip an "Object'Pkg.Object'" wrapper or ".Object" suffix to a package name.
    p = asset_path
    if "." in p and p.rsplit("/", 1)[-1].count(".") >= 1:
        p = p.split(".")[0]
    return p


def create_folder(path: str) -> dict:
    """Create a content folder under the active project (never invent /Game/...)."""
    if not (path or "").strip():
        raise ValueError(
            "path required — e.g. /MyProject/Materials/City from get_project_info().content_root "
            "(never /Game/... for new island assets)"
        )
    path = pin_project_folder(path, default_leaf="Materials")
    ok = unreal.EditorAssetLibrary.make_directory(path)
    return {"path": path, "created": bool(ok)}

def import_asset(source_file: str, destination_path: str, replace_existing: bool = True) -> dict:
    """Import a file (fbx/png/wav/...) into a content path under the active project."""
    if not os.path.isfile(source_file):
        raise ValueError(f"Source file not found: {source_file}")
    destination_path = pin_project_folder(destination_path, default_leaf="Imported")
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


def _project_directory(directory: str = "", *, default_leaf: str = "") -> str:
    """Pin save/fixup directories to content_root; retarget invented ``/Game/...``."""
    d = (directory or "").strip()
    if not d or d in ("/Game", "/Game/"):
        root = (content_root() or "").rstrip("/")
        if not root:
            raise RuntimeError("No project content_root — open an island project first")
        return f"{root}/{default_leaf}".rstrip("/") if default_leaf else root
    return pin_project_folder(d, default_leaf=default_leaf or "Content")


def fixup_redirectors(directory: str = "") -> dict:
    """Resolve and remove asset redirectors under a directory (defaults to project content_root)."""
    d = _project_directory(directory)
    if not d.endswith("/"):
        d = d + "/"
    ar = _asset_registry()
    filt = unreal.ARFilter(
        package_paths=[d],
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
    return {"directory": d, "fixed": len(redirectors)}


def save_directory(directory: str = "", only_if_dirty: bool = True) -> dict:
    """Save all assets under a content directory (defaults to project content_root)."""
    d = _project_directory(directory)
    ok = unreal.EditorAssetLibrary.save_directory(d, only_if_is_dirty=bool(only_if_dirty), recursive=True)
    return {"directory": d, "saved": bool(ok)}


register("create_folder")(create_folder)
register("import_asset")(import_asset)
register("export_asset")(export_asset)
register("get_dependencies")(get_dependencies)
register("get_referencers")(get_referencers)
register("validate_uefn_asset")(validate_uefn_asset)
register("fixup_redirectors")(fixup_redirectors)
register("save_directory")(save_directory)
