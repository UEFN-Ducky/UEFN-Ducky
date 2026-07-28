"""PCG registry tools: run and inspect Procedural Content Generation graphs."""

from __future__ import annotations

import unreal

from listener import lookup
from listener.dispatch import register
from listener.serialize import serialize


def _pcg_components(actor_path: str):
    actor = lookup.require_actor(actor_path)
    pcg_cls = getattr(unreal, "PCGComponent", None)
    if pcg_cls is None:
        raise RuntimeError("PCGComponent not available in this UEFN build")
    comps = actor.get_components_by_class(pcg_cls)
    if not comps:
        raise ValueError(f"No PCGComponent on actor: {actor_path}")
    return actor, comps


def pcg_generate(actor_path: str, force: bool = False) -> dict:
    """Run PCG generate on an actor with a PCGComponent."""
    actor, comps = _pcg_components(actor_path)
    generated = []
    for comp in comps:
        if force and hasattr(comp, "cleanup_local"):
            try:
                comp.cleanup_local(True)
            except Exception:
                pass
        comp.generate()
        generated.append(serialize(comp))
    return {"actor_path": actor.get_path_name(), "components": len(generated), "generated": True}


def pcg_get_graph_info(actor_path: str) -> dict:
    """Read PCG graph asset metadata from an actor's PCGComponent."""
    actor, comps = _pcg_components(actor_path)
    graphs = []
    for comp in comps:
        graph = None
        try:
            graph = comp.get_editor_property("graph")
        except Exception:
            try:
                graph = comp.get_editor_property("pcg_graph")
            except Exception:
                pass
        entry = {"component": serialize(comp)}
        if graph is not None:
            entry["graph_path"] = graph.get_path_name() if hasattr(graph, "get_path_name") else str(graph)
            entry["graph_name"] = graph.get_name() if hasattr(graph, "get_name") else str(graph)
        graphs.append(entry)
    return {"actor_path": actor.get_path_name(), "pcg": graphs, "count": len(graphs)}


register("pcg_generate")(pcg_generate)
register("pcg_get_graph_info")(pcg_get_graph_info)
