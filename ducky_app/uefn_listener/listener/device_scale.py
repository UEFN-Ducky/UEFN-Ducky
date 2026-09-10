"""Refuse actor scale on Fortnite Creative devices — no Unreal import."""

_IDENTITY_EPS = 1e-3


def _is_identity_scale(scale) -> bool:
    """Move/revert snapshots always include scale [1,1,1]. That is not a rescale."""
    if scale is None:
        return True
    try:
        if isinstance(scale, dict):
            vals = [scale.get("x"), scale.get("y"), scale.get("z")]
        else:
            vals = list(scale)[:3]
        return len(vals) == 3 and all(abs(float(v) - 1.0) <= _IDENTITY_EPS for v in vals)
    except (TypeError, ValueError):
        return False


def refuse_if_creative_device_scale(kind: str, scale) -> None:
    if _is_identity_scale(scale):
        return
    if kind == "creative_device":
        raise ValueError(
            "Refused: never scale Fortnite Creative devices (buttons, triggers, "
            "volumes, barriers, pads, granters, Island Settings). Actor scale / "
            "set_actor_scale3d breaks them. Location and rotation are fine. "
            "Resize via Details properties (Width / Height / Depth / zone / tiles) "
            "with SetDeviceProperty — names from GetDeviceProperties. Scale is for "
            "props, meshes, and custom assets only."
        )
