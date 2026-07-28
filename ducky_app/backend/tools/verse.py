"""Verse discovery tools: parse/search Verse digest files (offline-first)."""

from __future__ import annotations

from backend.bridge import send_command
from backend.json_util import tool_json
from backend.tools.plugin_gate import plugin_mcp_tool
from backend.tools import verse_digests


def _local_or_listener(local_fn, command: str, args: dict, pretty: bool) -> str:
    """Prefer on-disk digests; fall back to the UEFN listener when none exist."""
    try:
        if verse_digests.digests_available(digest_path=str(args.get("digest_path") or "")):
            return tool_json(local_fn(), pretty=pretty)
    except Exception:
        # Fall through to listener — local parse errors shouldn't strand the AI.
        pass
    return tool_json(send_command(command, args), pretty=pretty)


@plugin_mcp_tool("verse")
def list_verse_digests(pretty: bool = False) -> str:
    """List every Verse digest for the project with purpose blurbs and decl counts.

    Digests (listener offline OK — read from disk):
    - Fortnite.digest.verse — Epic devices / gameplay APIs
    - Verse.digest.verse — language + SceneGraph / Simulation
    - UnrealEngine.digest.verse — engine APIs exposed to Verse
    - Assets.digest.verse — this project's custom materials, meshes, prefabs as Verse ids

    Weapon/item *definitions* in the Content Browser → search_assets. Digests are the
    Verse API + Verse-visible asset identifiers.
    """
    return tool_json(verse_digests.list_verse_digests(), pretty=pretty)


@plugin_mcp_tool("verse")
def list_verse_types(
    kind: str = "",
    digest: str = "",
    name_filter: str = "",
    offset: int = 0,
    limit: int = 200,
    digest_path: str = "",
    pretty: bool = False,
) -> str:
    """Enumerate Verse declarations from digests (class/enum/struct/interface/module/…).

    kind: optional — class | enum | struct | interface | module | extension_function
    digest: basename filter — e.g. 'fortnite', 'assets', 'Verse'
    name_filter: substring on the name — e.g. '_device' for all device classes
    Paginated via offset/limit. Use get_verse_api(name) for full members.

    Listener offline OK. Content Browser weapons/items → search_assets.
    """
    return tool_json(
        verse_digests.list_verse_types(
            kind=kind,
            digest=digest,
            name_filter=name_filter,
            offset=offset,
            limit=limit,
            digest_path=digest_path,
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("verse")
def list_verse_devices(digest_path: str = "", pretty: bool = False) -> str:
    """List device class names from Verse digests (_device suffix or creative_device parent).

    Listener offline OK. Prefer list_verse_types(kind='class', name_filter='_device')
    when you need modules/parents too.
    """
    return _local_or_listener(
        lambda: verse_digests.list_verse_devices(digest_path=digest_path),
        "list_verse_devices",
        {"digest_path": digest_path},
        pretty,
    )


@plugin_mcp_tool("verse")
def search_verse_digest(
    query: str, digest_path: str = "", max_results: int = 50, pretty: bool = False
) -> str:
    """Search Verse digest text for a keyword (ranked: decl name, then docs, then lines).

    Listener offline OK. Digests can be ~1 MB — search, never dump. Pass digest_path=
    the Assets digest to search only custom project assets.
    """
    return _local_or_listener(
        lambda: verse_digests.search_verse_digest(
            query, digest_path=digest_path, max_results=max_results
        ),
        "search_verse_digest",
        {"query": query, "digest_path": digest_path, "max_results": max_results},
        pretty,
    )


@plugin_mcp_tool("verse")
def get_verse_api(
    name: str, digest_path: str = "", max_chars: int = 24000, pretty: bool = False
) -> str:
    """Extract the full digest definition for a Verse identifier (class/module/interface/enum/function).

    Ground truth for the running UEFN build — exact members, signatures, and doc
    comments. Use before writing Verse against an unfamiliar API (entity,
    mesh_component, a device, a generated prefab class from Assets.digest, ...).

    Listener offline OK — reads digests from disk.
    """
    return _local_or_listener(
        lambda: verse_digests.get_verse_api(
            name, digest_path=digest_path, max_chars=max_chars
        ),
        "get_verse_api",
        {"name": name, "digest_path": digest_path, "max_chars": max_chars},
        pretty,
    )


@plugin_mcp_tool("verse")
def list_verse_modules(digest_path: str = "", pretty: bool = False) -> str:
    """List Verse module names (with nesting and line spans) across the digest files.

    Listener offline OK.
    """
    return _local_or_listener(
        lambda: verse_digests.list_verse_modules(digest_path=digest_path),
        "list_verse_modules",
        {"digest_path": digest_path},
        pretty,
    )
