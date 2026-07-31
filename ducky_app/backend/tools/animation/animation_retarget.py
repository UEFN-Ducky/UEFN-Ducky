"""IK Rig / retarget tools: read, create, map chains, bake animations."""

from __future__ import annotations

from typing import Optional

from backend.bridge import send_command
from backend.util.json_util import tool_json
from backend.tools.support.plugin_gate import plugin_mcp_tool


@plugin_mcp_tool("animation")
def ik_retarget_capabilities(pretty: bool = False) -> str:
    """Report whether this UEFN build exposes the IK Rig / retarget API (call first)."""
    return tool_json(send_command("ik_retarget_capabilities", {}), pretty=pretty)


@plugin_mcp_tool("animation")
def list_skeleton_bones(skeletal_mesh_path: str, pretty: bool = False) -> str:
    """List a skeletal mesh's bone names and guess its chain preset (biped/mannequin)."""
    return tool_json(send_command("list_skeleton_bones", {"skeletal_mesh_path": skeletal_mesh_path}), pretty=pretty)


@plugin_mcp_tool("animation")
def get_retarget_preset(name: str = "", pretty: bool = False) -> str:
    """Return a retarget chain preset's root + chains; empty name lists all presets."""
    return tool_json(send_command("get_retarget_preset", {"name": name}), pretty=pretty)


@plugin_mcp_tool("animation")
def get_ik_rig_info(ik_rig_path: str, pretty: bool = False) -> str:
    """Read an IK Rig's retarget root and chains."""
    return tool_json(send_command("get_ik_rig_info", {"ik_rig_path": ik_rig_path}), pretty=pretty)


@plugin_mcp_tool("animation")
def get_ik_retargeter_info(ik_retargeter_path: str, pretty: bool = False) -> str:
    """Read an IK Retargeter's source/target rigs."""
    return tool_json(send_command("get_ik_retargeter_info", {"ik_retargeter_path": ik_retargeter_path}), pretty=pretty)


@plugin_mcp_tool("animation")
def create_ik_rig_asset(skeletal_mesh_path: str, dest_folder: str, name: str, pretty: bool = False) -> str:
    """Create an IK Rig asset and bind its skeletal mesh."""
    return tool_json(send_command("create_ik_rig_asset", {"skeletal_mesh_path": skeletal_mesh_path, "dest_folder": dest_folder, "name": name}), pretty=pretty)


@plugin_mcp_tool("animation")
def create_ik_retargeter_asset(source_ik_rig_path: str, target_ik_rig_path: str, dest_folder: str, name: str, pretty: bool = False) -> str:
    """Create an IK Retargeter and bind source + target IK Rigs."""
    return tool_json(send_command("create_ik_retargeter_asset", {"source_ik_rig_path": source_ik_rig_path, "target_ik_rig_path": target_ik_rig_path, "dest_folder": dest_folder, "name": name}), pretty=pretty)


@plugin_mcp_tool("animation")
def set_retarget_root(ik_rig_path: str, bone: str, pretty: bool = False) -> str:
    """Set an IK Rig's retarget root (pelvis/hips bone)."""
    return tool_json(send_command("set_retarget_root", {"ik_rig_path": ik_rig_path, "bone": bone}), pretty=pretty)


@plugin_mcp_tool("animation")
def add_retarget_chains(ik_rig_path: str, chains: list[dict], replace_existing: bool = False, pretty: bool = False) -> str:
    """Add or replace retarget chains on an IK Rig."""
    return tool_json(send_command("add_retarget_chains", {"ik_rig_path": ik_rig_path, "chains": chains, "replace_existing": replace_existing}), pretty=pretty)


@plugin_mcp_tool("animation")
def remove_retarget_chains(ik_rig_path: str, names: list[str], pretty: bool = False) -> str:
    """Remove named retarget chains from an IK Rig."""
    return tool_json(send_command("remove_retarget_chains", {"ik_rig_path": ik_rig_path, "names": names}), pretty=pretty)


@plugin_mcp_tool("animation")
def auto_map_retarget_chains(ik_retargeter_path: str, pretty: bool = False) -> str:
    """Auto-map source→target chains on an IK Retargeter."""
    return tool_json(send_command("auto_map_retarget_chains", {"ik_retargeter_path": ik_retargeter_path}), pretty=pretty)


@plugin_mcp_tool("animation")
def retarget_animation(
    ik_retargeter_path: str,
    source_mesh_path: str,
    target_mesh_path: str,
    anim_paths: list[str],
    prefix: str = "",
    suffix: str = "_Retargeted",
    pretty: bool = False,
) -> str:
    """Bake source AnimSequences onto the target skeleton via an IK Retargeter."""
    return tool_json(send_command("retarget_animation", {"ik_retargeter_path": ik_retargeter_path, "source_mesh_path": source_mesh_path, "target_mesh_path": target_mesh_path, "anim_paths": anim_paths, "prefix": prefix, "suffix": suffix}), pretty=pretty)


@plugin_mcp_tool("animation")
def retarget_animation_pipeline(
    source_mesh_path: str,
    target_mesh_path: str,
    anim_path: str,
    dest_folder: str = "",
    source_preset: str = "auto",
    target_preset: str = "auto",
    suffix: str = "_Retargeted",
    pretty: bool = False,
) -> str:
    """Chain IK rig + retargeter + bake for a single animation."""
    return tool_json(send_command("retarget_animation_pipeline", {"source_mesh_path": source_mesh_path, "target_mesh_path": target_mesh_path, "anim_path": anim_path, "dest_folder": dest_folder, "source_preset": source_preset, "target_preset": target_preset, "suffix": suffix}), pretty=pretty)


@plugin_mcp_tool("animation")
def list_skeleton_sockets(asset_path: str, pretty: bool = False) -> str:
    """List sockets on a Skeleton (pass a Skeleton or SkeletalMesh path)."""
    return tool_json(send_command("list_skeleton_sockets", {"asset_path": asset_path}), pretty=pretty)


@plugin_mcp_tool("animation")
def add_skeleton_socket(
    asset_path: str,
    bone_name: str,
    socket_name: str,
    location: Optional[list[float]] = None,
    rotation: Optional[list[float]] = None,
    update_existing: bool = False,
    pretty: bool = False,
) -> str:
    """Add a socket to a Skeleton under bone_name — NEVER build sockets via execute_python.

    asset_path may be the Skeleton or its SkeletalMesh. location=[x,y,z],
    rotation=[pitch,yaw,roll] relative to the bone. update_existing=true re-fits
    an existing socket. Attach afterwards via attach_actor(rule="snap_to_target")
    or a component's socket field.
    """
    return tool_json(
        send_command(
            "add_skeleton_socket",
            {
                "asset_path": asset_path,
                "bone_name": bone_name,
                "socket_name": socket_name,
                "location": location,
                "rotation": rotation,
                "update_existing": update_existing,
            },
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("animation")
def get_skeletal_mesh_info(asset_path: str, pretty: bool = False) -> str:
    """One-call skeletal mesh inspection: skeleton, bones, sockets, materials, LODs, bounds."""
    return tool_json(send_command("get_skeletal_mesh_info", {"asset_path": asset_path}), pretty=pretty)


@plugin_mcp_tool("animation")
def remove_skeleton_socket(asset_path: str, socket_name: str, pretty: bool = False) -> str:
    """Remove a named socket from a Skeleton (pass a Skeleton or SkeletalMesh path)."""
    return tool_json(send_command("remove_skeleton_socket", {"asset_path": asset_path, "socket_name": socket_name}), pretty=pretty)
