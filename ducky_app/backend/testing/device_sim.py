"""Offline device-graph simulator — pure Python, no UEFN needed once a snapshot exists."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from backend.testing.device_semantics import DEVICE_SEMANTICS


@lru_cache(maxsize=1)
def load_semantics() -> dict[str, Any]:
    # In-process catalog — never touches Temp/disk (packaged installs drop sibling JSON).
    return DEVICE_SEMANTICS


def _normalize_class(cls: str) -> str:
    text = (cls or "").strip()
    # Strip common UEFN suffixes
    for suffix in ("_C", "_device", "_Device"):
        if text.endswith(suffix) and suffix != "_device":
            text = text[: -len(suffix)]
    return text


def resolve_semantics(cls: str, kind: str = "") -> tuple[str, dict[str, Any]]:
    """Return (catalog_key, entry) for a device class, or verse_script / unknown."""
    catalog = load_semantics()
    raw = cls or ""
    low = raw.lower()
    norm = _normalize_class(raw).lower()

    for key, entry in catalog.items():
        if key.lower() == low or key.lower() == norm:
            return key, entry
        for alias in entry.get("aliases") or []:
            a = str(alias).lower()
            if a == low or a in low or low in a or a == norm:
                return key, entry

    if kind in ("verse_script", "verse_source") or "versedevice" in low:
        return "verse_script", catalog.get("verse_script") or {}
    return "unknown", {"emits": [], "receives": [], "effects": [], "aliases": []}


def _node_index(snapshot: dict) -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for node in snapshot.get("nodes") or []:
        for key in (node.get("id"), node.get("path"), node.get("label")):
            if key:
                idx[str(key)] = node
                idx[str(key).lower()] = node
    return idx


def _outgoing(snapshot: dict, node_id: str) -> list[dict]:
    out = []
    nid = str(node_id)
    for edge in snapshot.get("edges") or []:
        if str(edge.get("from")) == nid and edge.get("wired") and edge.get("to"):
            out.append(edge)
    return out


def simulate_device_event(
    snapshot: dict,
    device: str,
    event: str = "InteractedWithEvent",
    max_depth: int = 12,
) -> dict[str, Any]:
    """Propagate ``event`` from ``device`` through wired edges; return an ordered trace."""
    idx = _node_index(snapshot)
    start = idx.get(device) or idx.get(device.lower())
    if not start:
        return {
            "ok": False,
            "error": f"device not found in snapshot: {device!r}",
            "trace": [],
            "effects": [],
        }

    event_name = (event or "InteractedWithEvent").strip()
    # Queue of (node_id, incoming_event_or_fn, depth)
    queue: list[tuple[str, str, int]] = [(str(start["id"]), event_name, 0)]
    seen: set[tuple[str, str]] = set()
    trace: list[dict] = []
    effects: list[dict] = []

    # Common event → receive mapping for creative device chains
    event_to_receive = {
        "InteractedWithEvent": ("Trigger", "GrantItem", "Activate", "Play", "Show", "Teleport"),
        "PressedEvent": ("Trigger", "GrantItem", "Activate"),
        "TriggeredEvent": ("GrantItem", "Activate", "Play", "Show", "Teleport", "AddScore"),
        "ActivatedEvent": ("GrantItem", "Play", "Show", "Teleport"),
        "AgentEntersEvent": ("Trigger", "GrantItem", "Activate", "Teleport"),
        "SuccessEvent": ("GrantItem", "Activate", "Play", "Show"),
        "SpawnedEvent": ("GrantItem", "Show", "Activate"),
    }

    while queue:
        node_id, incoming, depth = queue.pop(0)
        if depth > max_depth:
            continue
        key = (node_id, incoming)
        if key in seen:
            continue
        seen.add(key)

        node = idx.get(node_id) or idx.get(node_id.lower())
        if not node:
            continue

        sem_key, sem = resolve_semantics(str(node.get("class") or ""), str(node.get("kind") or ""))
        emits = list(sem.get("emits") or [])
        receives = list(sem.get("receives") or [])
        node_effects = list(sem.get("effects") or [])

        step = {
            "depth": depth,
            "device": node.get("label") or node_id,
            "id": node_id,
            "class": node.get("class"),
            "kind": node.get("kind"),
            "semantics": sem_key,
            "incoming": incoming,
            "emits": [],
            "effects": [],
        }

        # Does this node accept the incoming signal?
        accepts = (
            incoming in receives
            or "*" in receives
            or incoming in emits  # source emitting
            or depth == 0
            or sem_key == "verse_script"
            or sem_key == "unknown"
        )
        if not accepts and depth > 0:
            step["skipped"] = True
            step["reason"] = f"{sem_key} does not receive {incoming!r}"
            trace.append(step)
            continue

        # Apply effects when we receive an action (not just emit at source)
        if depth > 0 or incoming in receives:
            for eff in node_effects:
                effects.append(
                    {
                        "device": node.get("label") or node_id,
                        "kind": eff.get("kind"),
                        "detail": eff.get("detail"),
                    }
                )
                step["effects"].append(eff)

        # What does this node emit next?
        next_events: list[str] = []
        if depth == 0:
            # Source: fire the named event (or its catalog emits)
            if incoming in emits or "*" in emits or not emits:
                next_events = [incoming] if incoming else list(emits[:1])
            else:
                next_events = list(emits[:1]) or [incoming]
        else:
            # Downstream: after receiving, emit its outbound events
            next_events = [e for e in emits if e != "*"][:3]
            if not next_events and sem_key == "verse_script":
                next_events = ["TriggeredEvent"]

        step["emits"] = next_events
        trace.append(step)

        # Fan out along wired edges
        for edge in _outgoing(snapshot, node_id):
            target = str(edge.get("to") or "")
            if not target:
                continue
            # Map emitted events to likely receive functions on the target
            candidates: list[str] = []
            for ev in next_events:
                candidates.extend(event_to_receive.get(ev, ()))
                candidates.append(ev)
            if not candidates:
                candidates = ["Trigger", "Activate", "GrantItem"]
            # Deduplicate while preserving order
            seen_cand: list[str] = []
            for c in candidates:
                if c not in seen_cand:
                    seen_cand.append(c)
            # Enqueue one best guess — the first that the target semantics receive
            target_node = idx.get(target) or idx.get(target.lower())
            if target_node:
                _tk, tsem = resolve_semantics(
                    str(target_node.get("class") or ""), str(target_node.get("kind") or "")
                )
                trecv = list(tsem.get("receives") or [])
                chosen = None
                for c in seen_cand:
                    if c in trecv or "*" in trecv or not trecv:
                        chosen = c
                        break
                if chosen is None:
                    chosen = seen_cand[0]
            else:
                chosen = seen_cand[0]
            queue.append((target, chosen, depth + 1))

    note = ""
    if not (snapshot.get("edges") or []):
        if str(start.get("kind") or "") == "verse_source":
            note = (
                "Source-only class (not placed). Sim cannot follow wiring until the device "
                "is in the level — open UEFN, connect the listener, place + wire it, then Sim again."
            )
        else:
            note = "No wiring edges in snapshot — simulation is shallow."

    return {
        "ok": True,
        "device": start.get("label") or device,
        "event": event_name,
        "trace": trace,
        "effects": effects,
        "steps": len(trace),
        "effect_count": len(effects),
        "note": note,
    }


def device_graph_audit(snapshot: dict) -> dict[str, Any]:
    """Find unwired refs, orphan devices, and simple island-setup issues."""
    nodes = list(snapshot.get("nodes") or [])
    edges = list(snapshot.get("edges") or [])
    idx = _node_index(snapshot)

    unwired = []
    for edge in edges:
        if edge.get("wired") is False or not edge.get("to"):
            unwired.append(
                {
                    "device": edge.get("from"),
                    "field": edge.get("field"),
                    "kind": edge.get("kind"),
                    "verse_type": edge.get("verse_type"),
                }
            )

    # Resolve edge targets to known node ids
    incoming: set[str] = set()
    outgoing: set[str] = set()
    dangling = []
    for edge in edges:
        if not edge.get("wired") or not edge.get("to"):
            continue
        frm = str(edge.get("from") or "")
        to = str(edge.get("to") or "")
        outgoing.add(frm)
        target = idx.get(to) or idx.get(to.lower())
        if target:
            incoming.add(str(target["id"]))
        else:
            dangling.append(
                {
                    "from": frm,
                    "to": to,
                    "field": edge.get("field"),
                    "kind": edge.get("kind"),
                }
            )

    orphans = []
    for node in nodes:
        nid = str(node.get("id") or "")
        if nid not in incoming and nid not in outgoing:
            # Spawn pads / island settings are expected roots — skip soft orphans
            cls = str(node.get("class") or "").lower()
            if "spawner" in cls or "experience" in cls or "island" in cls:
                continue
            orphans.append(
                {
                    "id": nid,
                    "label": node.get("label"),
                    "class": node.get("class"),
                    "kind": node.get("kind"),
                }
            )

    spawn_pads = [
        n
        for n in nodes
        if "spawner" in str(n.get("class") or "").lower()
        or "spawn" in str(n.get("label") or "").lower()
    ]

    # Cycle detection (simple DFS on wired edges)
    graph: dict[str, list[str]] = {}
    for edge in edges:
        if edge.get("wired") and edge.get("to"):
            frm = str(edge["from"])
            to = str(edge["to"])
            target = idx.get(to) or idx.get(to.lower())
            tid = str(target["id"]) if target else to
            graph.setdefault(frm, []).append(tid)

    cycles: list[list[str]] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def dfs(node: str, path: list[str]) -> None:
        if node in visiting:
            if node in path:
                cycles.append(path[path.index(node) :] + [node])
            return
        if node in visited:
            return
        visiting.add(node)
        for nxt in graph.get(node, []):
            dfs(nxt, path + [node])
        visiting.remove(node)
        visited.add(node)

    for n in list(graph):
        dfs(n, [])

    issues = []
    if unwired:
        issues.append({"severity": "error", "code": "unwired_refs", "count": len(unwired)})
    if dangling:
        issues.append({"severity": "error", "code": "dangling_targets", "count": len(dangling)})
    if orphans:
        issues.append({"severity": "warn", "code": "orphan_devices", "count": len(orphans)})
    if cycles:
        issues.append({"severity": "warn", "code": "cycles", "count": len(cycles)})
    if not spawn_pads:
        issues.append(
            {
                "severity": "warn",
                "code": "no_spawn_pads",
                "detail": "No player spawn pads found — MaxPlayers needs one pad per slot",
            }
        )

    return {
        "ok": True,
        "issues": issues,
        "unwired": unwired,
        "dangling": dangling,
        "orphans": orphans,
        "cycles": cycles[:10],
        "spawn_pad_count": len(spawn_pads),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "summary": {
            "errors": sum(1 for i in issues if i.get("severity") == "error"),
            "warnings": sum(1 for i in issues if i.get("severity") == "warn"),
        },
    }


def scan_verse_devices_from_files(project_root: str) -> dict[str, Any]:
    """Offline discovery: parse Verse/**/*.verse for creative_device subclasses."""
    import re
    from pathlib import Path

    root = Path(project_root)
    verse_roots = [root / "Verse", root]
    class_re = re.compile(
        r"^(\w+)\s*(?:<[^>]*>\s*)*:=\s*class\s*\(\s*creative_device\b",
        re.M,
    )
    editable_re = re.compile(
        r"@editable\b[^\n]*\n\s*(\w+)\s*:\s*([^\s=]+)",
        re.M,
    )
    nodes = []
    seen: set[str] = set()
    for vroot in verse_roots:
        if not vroot.is_dir():
            continue
        for path in vroot.rglob("*.verse"):
            if "digest" in path.name.lower():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            for m in class_re.finditer(text):
                name = m.group(1)
                if name in seen:
                    continue
                seen.add(name)
                # Editables in the class body (best-effort: from match to next class)
                start = m.start()
                nxt = class_re.search(text, m.end())
                body = text[start : nxt.start() if nxt else len(text)]
                editables = [
                    {"field": em.group(1), "verse_type": em.group(2).rstrip(",")}
                    for em in editable_re.finditer(body)
                ]
                nodes.append(
                    {
                        "id": f"verse://{rel}#{name}",
                        "label": name,
                        "class": name,
                        "kind": "verse_source",
                        "path": "",
                        "verse_source": rel,
                        "editables": {e["field"]: e for e in editables},
                        "placed": False,
                    }
                )
    return {"nodes": nodes, "edges": [], "count": len(nodes), "source": "workspace"}
