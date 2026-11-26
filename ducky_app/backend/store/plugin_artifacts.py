"""One Store file per platform for a plugin version.

A version record keeps its Windows zip in the top-level fields (``sha256``, ``size``, ``file``),
exactly as before, so apps that predate Linux builds keep installing Windows. Every build,
Windows included, is also listed in ``artifacts``::

    {"os": "linux", "arch": "x86_64", "file": "blender-linux-x86_64.zip", "sha256": "...", "size": 123}

A source plugin runs anywhere: its one artifact is ``os``/``arch`` ``"any"``.
"""

from __future__ import annotations

from typing import Any

from backend.uefn_plugins import manifest_platform as mp

ANY = "any"
_FILE_FIELDS = ("file", "sha256", "size")


def artifact_file_name(plugin_id: str, os_name: str, arch: str) -> str:
    if os_name == ANY:
        return f"{plugin_id}.zip"
    return f"{plugin_id}-{os_name}-{mp.norm_arch(arch)}.zip"


def artifact_for_build(plugin_id: str, manifest: dict[str, Any], sha256: str, size: int) -> dict[str, Any]:
    """The artifact entry for one built zip, its target read from the zip's plugin.json."""
    os_name, arch = mp.manifest_target(manifest) or (ANY, ANY)
    if not os_name:
        raise ValueError(f"unknown build target {manifest.get('python_abi')!r}")
    return {"os": os_name, "arch": arch, "file": artifact_file_name(plugin_id, os_name, arch),
            "sha256": sha256, "size": int(size)}


def record_artifacts(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Every file of a version. A record from before ``artifacts`` is one file: Windows when compiled."""
    listed = record.get("artifacts")
    if isinstance(listed, list) and listed:
        return [a for a in listed if isinstance(a, dict)]
    one = {k: record[k] for k in _FILE_FIELDS if k in record}
    if record.get("compiled"):
        return [{"os": mp.WINDOWS, "arch": mp.X86_64, **one}]
    return [{"os": ANY, "arch": ANY, **one}]


def pick_artifact(record: dict[str, Any], os_name: str | None = None, arch: str | None = None) -> dict[str, Any] | None:
    """The file this machine installs: its own OS and CPU, else a source build, else None."""
    want_os, want_arch = os_name or mp.host_os(), mp.norm_arch(arch or mp.host_arch())
    files = record_artifacts(record)
    for a in files:
        if a.get("os") == want_os and mp.norm_arch(a.get("arch", "")) == want_arch:
            return a
    return next((a for a in files if a.get("os") == ANY), None)


def download_problem(payload: dict[str, Any], os_name: str | None = None, arch: str | None = None) -> str:
    """Why a Store download response is not this machine's build, '' when it is.

    The response names its file on top (``sha256``) and, from a Store with per-platform builds,
    lists them all in ``artifacts``. ``compiled`` rides the signed record when not on top.
    """
    rec = dict(payload)
    sig = payload.get("signature")
    if "compiled" not in rec and isinstance(sig, dict):
        rec["compiled"] = sig.get("compiled")
    art = pick_artifact(rec, os_name, arch)
    want_os, want_arch = os_name or mp.host_os(), mp.norm_arch(arch or mp.host_arch())
    label = mp._OS_LABELS.get(want_os, want_os)
    cpu = mp.arch_label(want_os, want_arch)
    if art is None:
        same_os = any(a.get("os") == want_os for a in record_artifacts(rec))
        return f"This plugin has no {label} {cpu} build yet" if same_os else f"This plugin has no {label} build yet"
    if str(art.get("sha256") or "").lower() != str(payload.get("sha256") or "").lower():
        return f"The Store sent a build for another platform, not {label} {cpu}. Try again later."
    return ""


def ask_for_this_platform(body: dict[str, Any]) -> bool:
    """Off Windows x64, name this OS/CPU in a Store download request; Windows x64 asks as it
    always has (the Store's top-level file is its build)."""
    if mp.host_os() == mp.WINDOWS and mp.host_arch() == mp.X86_64:
        return False
    body.update(os=mp.host_os(), arch=mp.host_arch())
    return True


def refuse_other_build(payload: dict[str, Any], other_os: bool, error: type[Exception]) -> None:
    """Raise ``error(reason, code="no_platform_build")`` when the download is not this PC's build,
    before anything is fetched or written. A refused response (no sha256) keeps its own reason."""
    problem = download_problem(payload) if other_os and payload.get("sha256") else ""
    if problem:
        raise error(problem, code="no_platform_build")  # type: ignore[call-arg]


def put_artifact(record: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    """The record with ``artifact`` added, replacing the one for its platform. Windows x64 (or a
    source build) also fills the top-level fields old apps read."""
    out = dict(record)
    key = (artifact["os"], mp.norm_arch(artifact["arch"]))
    out["artifacts"] = [a for a in record_artifacts(record) if (a.get("os"), mp.norm_arch(a.get("arch", ""))) != key
                        and a.get(_FILE_FIELDS[1])] + [artifact]
    if key == (mp.WINDOWS, mp.X86_64) or artifact["os"] == ANY:
        out.update({k: artifact[k] for k in _FILE_FIELDS if k in artifact})
    return out


def legacy_view(record: dict[str, Any]) -> dict[str, Any]:
    """What an app older than Linux builds is served: the Windows file only, no ``artifacts``."""
    out = {k: v for k, v in record.items() if k != "artifacts"}
    win = pick_artifact(record, mp.WINDOWS, mp.X86_64)
    if win is None:
        return {}  # nothing an old (Windows-only) app can install
    out.update({k: win[k] for k in _FILE_FIELDS if k in win})
    return out
