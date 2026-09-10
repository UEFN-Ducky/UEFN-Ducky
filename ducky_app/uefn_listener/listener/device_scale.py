"""Refuse actor scale on Fortnite Creative devices — no Unreal import."""


def refuse_if_creative_device_scale(kind: str, scale) -> None:
    if scale is None:
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
