"""Domain command modules — imported for their ``listener.dispatch`` @register side effects.

Each module here registers its commands via ``listener.dispatch.register``; importing this package
runs those registrations. There is no separate tool registry — one dispatch system for all commands.
"""

from listener.registry import (  # noqa: F401  (side-effect imports: register commands)
    animation_retarget,
    assets_pipeline,
    blockout_areas,
    data_tables,
    device_graph,
    editor_control,
    fort,
    introspection,
    level,
    level_design,
    materials,
    modeling,
    niagara,
    pcg,
    scene_graph,
    sequencer,
    umg,
    verse,
    worldgen,
)
