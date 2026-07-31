"""Animation authoring tools: Level Sequences and AnimSequence keyframes."""

from __future__ import annotations

from backend.bridge import send_command
from backend.util.json_util import tool_json
from backend.tools.support.plugin_gate import plugin_mcp_tool


@plugin_mcp_tool("animation")
def anim_author_capabilities(pretty: bool = False) -> str:
    """Report whether this UEFN build exposes Level Sequence / AnimSequence authoring APIs (call first)."""
    return tool_json(send_command("anim_author_capabilities", {}), pretty=pretty)


@plugin_mcp_tool("animation")
def create_level_sequence(dest_folder: str, name: str, fps: int = 30, length_seconds: float = 5.0, pretty: bool = False) -> str:
    """Create a LevelSequence asset (display rate + playback length); play it via a Cinematic Sequence device."""
    return tool_json(
        send_command(
            "create_level_sequence",
            {"dest_folder": dest_folder, "name": name, "fps": fps, "length_seconds": length_seconds},
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("animation")
def add_sequence_binding(sequence_path: str, actor_path: str, pretty: bool = False) -> str:
    """Bind a placed level actor into a LevelSequence; returns the binding name for add_transform_keys."""
    return tool_json(
        send_command("add_sequence_binding", {"sequence_path": sequence_path, "actor_path": actor_path}),
        pretty=pretty,
    )


@plugin_mcp_tool("animation")
def add_transform_keys(sequence_path: str, binding_name: str, keys: list[dict], pretty: bool = False) -> str:
    """Key a sequence binding's transform. keys=[{time(s), location?[x,y,z], rotation?[roll,pitch,yaw]deg, scale?[x,y,z], interp?(auto|linear|constant)}]. Auto-extends the section range over all keys."""
    return tool_json(
        send_command(
            "add_transform_keys",
            {"sequence_path": sequence_path, "binding_name": binding_name, "keys": keys},
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("animation")
def get_sequence_info(sequence_path: str, pretty: bool = False) -> str:
    """Read a LevelSequence's playback range, bindings, tracks, and key counts."""
    return tool_json(send_command("get_sequence_info", {"sequence_path": sequence_path}), pretty=pretty)


@plugin_mcp_tool("animation")
def create_anim_sequence(
    skeletal_mesh_path: str, dest_folder: str, name: str, length_seconds: float = 1.0, fps: int = 30, pretty: bool = False
) -> str:
    """Create an empty AnimSequence bound to a skeletal mesh's skeleton (then set_anim_bone_keys)."""
    return tool_json(
        send_command(
            "create_anim_sequence",
            {
                "skeletal_mesh_path": skeletal_mesh_path,
                "dest_folder": dest_folder,
                "name": name,
                "length_seconds": length_seconds,
                "fps": fps,
            },
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("animation")
def set_anim_bone_keys(anim_path: str, bone: str, keys: list[dict], pretty: bool = False) -> str:
    """Key one bone's BONE-LOCAL transform. keys=[{time(s), location?, rotation?[roll,pitch,yaw]deg, scale?}]; sparse keys are resampled to every frame (crash-safe arrays)."""
    return tool_json(
        send_command("set_anim_bone_keys", {"anim_path": anim_path, "bone": bone, "keys": keys}),
        pretty=pretty,
    )


@plugin_mcp_tool("animation")
def get_anim_sequence_info(anim_path: str, pretty: bool = False) -> str:
    """Read an AnimSequence's length, fps, and tracked bones."""
    return tool_json(send_command("get_anim_sequence_info", {"anim_path": anim_path}), pretty=pretty)
