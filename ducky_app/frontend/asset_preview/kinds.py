"""Classify Unreal assets for panel preview actions (mesh / material / texture / niagara)."""

from __future__ import annotations

from pathlib import Path


_MATERIAL_CLASSES = frozenset(
    {
        "material",
        "materialinstance",
        "materialinstanceconstant",
        "materialinstancedynamic",
    }
)

_MESH_CLASSES = frozenset({"staticmesh"})

_TEXTURE_CLASSES = frozenset(
    {
        "texture2d",
        "texturecube",
        "texture2darray",
        "volumetexture",
        "texturerendertarget2d",
        "virtualtexture2d",
    }
)


def normalize_asset_class(asset_class: str) -> str:
    return (asset_class or "").strip()


def guess_preview_kind(relative_path: str, asset_class: str = "") -> str:
    """Return static_mesh | material | texture | niagara | other."""
    cls = normalize_asset_class(asset_class).lower()
    # Redirectors are stubs for moved assets — ignore class and use path heuristics.
    if cls == "objectredirector":
        cls = ""
    if cls in _MESH_CLASSES:
        return "static_mesh"
    if cls in _MATERIAL_CLASSES:
        return "material"
    if cls in _TEXTURE_CLASSES or cls.startswith("texture"):
        return "texture"
    if "niagara" in cls or cls in {"particlesystem", "particle_system"}:
        return "niagara"

    path = (relative_path or "").strip().replace("\\", "/").lower()
    stem = Path(path).stem.lower()
    if "/materials/" in path or stem.startswith(("m_", "mi_")):
        return "material"
    if "/textures/" in path or "/texture/" in path or stem.startswith(("t_", "tex_")):
        return "texture"
    if "/fx/" in path or "/niagara" in path or stem.startswith(("ns_", "ne_", "np_", "fx_")):
        return "niagara"
    if stem.startswith("sm_"):
        return "static_mesh"
    return "other"


def supports_mesh_preview(relative_path: str, asset_class: str = "") -> bool:
    return guess_preview_kind(relative_path, asset_class) == "static_mesh"


def supports_material_preview(relative_path: str, asset_class: str = "") -> bool:
    return guess_preview_kind(relative_path, asset_class) == "material"


def supports_texture_preview(relative_path: str, asset_class: str = "") -> bool:
    return guess_preview_kind(relative_path, asset_class) == "texture"


def clean_listener_error(message: str) -> str:
    """Strip traceback / command wrapper noise for UI."""
    text = (message or "").strip()
    if "Traceback" in text:
        text = text.split("Traceback", 1)[0].strip()
    # "UEFN command 'x' failed: Not a StaticMesh..."
    marker = "failed:"
    if marker in text.lower():
        idx = text.lower().rfind(marker)
        text = text[idx + len(marker) :].strip() or text
    return text[:240] or "Preview failed"
