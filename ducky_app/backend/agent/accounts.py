"""Any number of accounts (API keys or CLI logins) per provider.

Account ``"default"`` always exists: today's key (secret ``provider``) or today's CLI
login (the CLI's own config folder), so existing setups need no migration. Every other
account keeps its key as secret ``provider@<id>`` and, for coding-agent logins, its own
config folder under Ducky's data.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from backend.agent import secrets as _secrets
from frontend.settings import default_app_data_dir

DEFAULT = "default"
EXHAUSTED_S = 15 * 60
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

# ponytail: in-process lock; another process writing at the same instant can lose one
# edit (atomic replace, never a torn file). Move to the ducky.db kv if that bites.
_LOCK = threading.RLock()


def _data_root() -> Path:
    return default_app_data_dir()


def _state_path() -> Path:
    return _data_root() / "accounts.json"


def _load() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _provider(data: dict, provider: str) -> dict:
    p = data.setdefault("providers", {}).setdefault(provider, {})
    accounts = p.setdefault("accounts", [])
    if not any(a.get("id") == DEFAULT for a in accounts):
        accounts.insert(0, {"id": DEFAULT, "label": "Default", "kind": ""})
    return p


def _find(p: dict, account_id: str) -> dict:
    for a in p["accounts"]:
        if a.get("id") == account_id:
            return a
    raise KeyError(f"no account {account_id!r}")


def _until(p: dict, account_id: str, now: float) -> float | None:
    t = p.get("exhausted", {}).get(account_id)
    return float(t) if t and float(t) > now else None


def list_accounts(provider: str) -> list[dict]:
    with _LOCK:
        p = _provider(_load(), provider)
    active, now = p.get("active") or DEFAULT, time.time()
    if not any(a["id"] == active for a in p["accounts"]):
        active = DEFAULT
    return [
        {
            "id": a["id"],
            "label": a.get("label") or a["id"],
            "kind": a.get("kind") or "key",
            "active": a["id"] == active,
            "exhausted_until": _until(p, a["id"], now),
        }
        for a in p["accounts"]
    ]


def add_account(provider: str, label: str, kind: str = "key") -> str:
    if kind not in ("key", "login"):
        raise ValueError("kind must be 'key' or 'login'")
    with _LOCK:
        data = _load()
        p = _provider(data, provider)
        taken = {a["id"] for a in p["accounts"]}
        new_id = uuid.uuid4().hex[:4]
        while new_id in taken:
            new_id = uuid.uuid4().hex[:4]
        default = _find(p, DEFAULT)
        if not default.get("kind"):
            default["kind"] = kind  # a provider's accounts are all keys or all logins
        label = (label or "").strip() or f"Account {len(taken) + 1}"
        p["accounts"].append({"id": new_id, "label": label, "kind": kind})
        _save(data)
    return new_id


def rename_account(provider: str, account_id: str, label: str) -> None:
    with _LOCK:
        data = _load()
        _find(_provider(data, provider), account_id)["label"] = (label or "").strip() or account_id
        _save(data)


def remove_account(provider: str, account_id: str) -> None:
    """Forget one account, its secret and its config folder. ``default`` stays."""
    if account_id == DEFAULT:
        raise ValueError("the default account can't be removed")
    with _LOCK:
        data = _load()
        p = _provider(data, provider)
        p["accounts"].remove(_find(p, account_id))
        p.get("exhausted", {}).pop(account_id, None)
        if p.get("active") == account_id:
            p["active"] = DEFAULT
        _save(data)
    _secrets.clear_key(_secret_name(provider, account_id))
    _remove_tree(_account_dir(provider, account_id))


def reorder_accounts(provider: str, ids: list[str]) -> None:
    """Put accounts in ``ids`` order; ones not named keep their order after them."""
    with _LOCK:
        data = _load()
        p = _provider(data, provider)
        rank = {i: n for n, i in enumerate(ids)}
        p["accounts"].sort(key=lambda a: rank.get(a["id"], len(rank)))  # stable sort
        _save(data)


def active_account(provider: str) -> str:
    return next(a["id"] for a in list_accounts(provider) if a["active"])


def set_active_account(provider: str, account_id: str) -> None:
    with _LOCK:
        data = _load()
        p = _provider(data, provider)
        _find(p, account_id)
        p["active"] = account_id
        _save(data)


def auto_switch_enabled(provider: str) -> bool:
    with _LOCK:
        return _provider(_load(), provider).get("auto_switch", True) is not False


def set_auto_switch(provider: str, on: bool) -> None:
    with _LOCK:
        data = _load()
        _provider(data, provider)["auto_switch"] = bool(on)
        _save(data)


def mark_exhausted(provider: str, account_id: str, until: float | None = None) -> None:
    """This account hit its limit until ``until`` (epoch seconds; default 15 min)."""
    with _LOCK:
        data = _load()
        p = _provider(data, provider)
        _find(p, account_id)
        p.setdefault("exhausted", {})[account_id] = float(until) if until else time.time() + EXHAUSTED_S
        _save(data)


def exhausted_until(provider: str, account_id: str) -> float | None:
    with _LOCK:
        return _until(_provider(_load(), provider), account_id, time.time())


def pick_account(provider: str, *, is_full: Callable[[str], bool] | None = None) -> str:
    """The account to run as now.

    The picked one, unless it is full and auto-switch is on; then the next account in
    order that isn't; all full → the one whose limit resets first. ``is_full(id)`` adds
    a live check on top of the recorded exhaustion.
    """
    accounts = list_accounts(provider)
    start = next(n for n, a in enumerate(accounts) if a["active"])
    if not auto_switch_enabled(provider):
        return accounts[start]["id"]
    ring = accounts[start:] + accounts[:start]
    for a in ring:
        if a["exhausted_until"] is None and not (is_full and is_full(a["id"])):
            return a["id"]
    return min(ring, key=lambda a: a["exhausted_until"] or float("inf"))["id"]


def _secret_name(provider: str, account_id: str) -> str:
    return provider if account_id == DEFAULT else f"{provider}@{account_id}"


def account_key(provider: str, account_id: str) -> str | None:
    return _secrets.get_key(_secret_name(provider, account_id))


def set_account_key(provider: str, account_id: str, value: str) -> None:
    with _LOCK:
        _find(_provider(_load(), provider), account_id)
    _secrets.set_key(_secret_name(provider, account_id), value)


def _account_dir(provider: str, account_id: str) -> Path:
    return _data_root() / "coding_agents" / "accounts" / provider / account_id


def account_config_dir(provider: str, account_id: str) -> Path | None:
    """The account's own CLI config folder (created); None for ``default`` (the CLI's own)."""
    if account_id == DEFAULT:
        return None
    folder = _account_dir(provider, account_id)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _is_link(path: Path) -> bool:
    """A symlink or a Windows junction (any reparse point)."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


def _unlink(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        os.rmdir(path)  # a directory junction / symlink: removes the link, never the target


def _remove_tree(root: Path) -> None:
    """Delete an account folder; links into the shared folder are unlinked, never followed."""
    if not os.path.lexists(root):
        return
    if _is_link(root):
        _unlink(root)
        return
    for entry in list(root.iterdir()):
        if _is_link(entry):
            _unlink(entry)
        elif entry.is_dir():
            _remove_tree(entry)
        else:
            entry.unlink()
    root.rmdir()


def _link_dir(src: Path, dst: Path) -> None:
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(src), str(dst))  # No admin / Developer Mode needed.
    else:
        os.symlink(src, dst, target_is_directory=True)


# --- Coding-agent logins: hooks the gateway plugin passes to register_coding_agent ---
# account_env(dir) -> env · account_login(dir) -> {argv, env} · account_status(dir) ->
# {loggedIn, email?, plan?} · account_logout(dir) · account_shared={"dirs", "files",
# "primary": the CLI's own config folder (path or () -> path), linked into each account}.
# ``dir`` is None for ``default`` (the CLI's own folder).

_CURRENT: dict[str, str] = {}  # agent id -> account its last launch ran as


def _agent_reg(agent_id: str) -> dict:
    from backend.uefn_plugins.host import get_coding_agent_registration

    return get_coding_agent_registration(agent_id) or {}


def _call_hook(agent_id: str, hook: str, account_id: str):
    fn = _agent_reg(agent_id).get(hook)
    if not callable(fn):
        raise ValueError(f"{agent_id} has no {hook}")
    _find(_provider(_load(), agent_id), account_id)
    return fn(account_config_dir(agent_id, account_id))


def coding_agent_account_env(agent_id: str) -> dict[str, str]:
    """Extra launch env that runs the CLI as the account picked now ({} for ``default``)."""
    account = pick_account(agent_id)
    _CURRENT[agent_id] = account
    reg = _agent_reg(agent_id)
    fn = reg.get("account_env")
    folder = account_config_dir(agent_id, account)
    if folder is None or not callable(fn):
        return {}
    shared = reg.get("account_shared") or {}
    primary = shared.get("primary")
    if callable(primary):
        primary = primary()
    if primary:
        share_config(Path(primary), folder, shared.get("dirs") or (), shared.get("files") or ())
    env = fn(folder)
    return {str(k): str(v) for k, v in (env or {}).items()}


def current_account(agent_id: str) -> str:
    """The account the agent's last launch ran as (else the picked one)."""
    return _CURRENT.get(agent_id) or active_account(agent_id)


def report_limit(agent_id: str, account_id: str | None = None, reset_at: float | None = None) -> None:
    """An adapter saw a usage-limit error: that account is full until ``reset_at``."""
    mark_exhausted(agent_id, account_id or current_account(agent_id), reset_at)


def account_login(agent_id: str, account_id: str) -> dict:
    """``{argv, env}`` that signs this account in, from the plugin's ``account_login``."""
    out = _call_hook(agent_id, "account_login", account_id) or {}
    argv = [str(a) for a in out.get("argv") or ()]
    if not argv:
        raise ValueError(f"{agent_id} returned no login command")
    return {"argv": argv, "env": {str(k): str(v) for k, v in (out.get("env") or {}).items()}}


def account_status(agent_id: str, account_id: str) -> dict:
    return dict(_call_hook(agent_id, "account_status", account_id) or {"loggedIn": False})


def account_logout(agent_id: str, account_id: str) -> None:
    _call_hook(agent_id, "account_logout", account_id)


def share_config(primary_dir: Path, account_dir: Path, dirs=(), files=()) -> None:
    """Link the primary login's shared folders into an account's and copy its newer
    shared files. A folder the account already has for real is left alone. Never raises."""
    primary_dir, account_dir = Path(primary_dir), Path(account_dir)
    try:
        if primary_dir.resolve() == account_dir.resolve():
            return
        account_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    for name in dirs:
        src, dst = primary_dir / name, account_dir / name
        try:
            if src.is_dir() and not os.path.lexists(dst):
                _link_dir(src, dst)
        except OSError:
            pass
    for name in files:
        src, dst = primary_dir / name, account_dir / name
        try:
            if src.is_file() and (not dst.is_file() or dst.stat().st_mtime < src.stat().st_mtime):
                shutil.copy2(src, dst)
        except OSError:
            pass
