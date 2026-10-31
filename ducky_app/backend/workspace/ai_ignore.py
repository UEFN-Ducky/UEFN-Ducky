"""User-owned AI file denials. Checked before reading bytes or modifying disk.

These guards protect Ducky-mediated operations, not an arbitrary process running
as the user's Windows account. Unsandboxed execution must be refused separately.
No negation rules: an AI must never be able to unignore a protected path.
"""
from __future__ import annotations

import fnmatch
import functools
import ntpath
import os
import threading
from dataclasses import dataclass

BUILTIN_RULES = (".env", ".env.*")
DENIED = "AI_FILE_IGNORED: access denied by the user's AI ignore list."
POLICY_UNAVAILABLE = "AI_FILE_POLICY_UNAVAILABLE: access refused."
protection_lock = threading.RLock()


def save_user_policy(patch: dict) -> str:
    """UI-only save; enabling/changing denials requires agents to be stopped.

    The human may disable the extra execution restriction immediately. This
    does not remove built-in or user-owned file input rules.
    """
    from frontend.settings import PanelSettings
    from frontend.ui_web.live_agent_runs import get_live_run_ids
    with protection_lock:
        disabling_strict = patch == {"ai_ignore_strict": False}
        if get_live_run_ids() and not disabling_strict:
            raise ValueError("Stop active agents before changing AI file protection.")
        settings = PanelSettings.load()
        if "ai_ignore_patterns" in patch:
            settings.ai_ignore_patterns = normalize_rules(patch["ai_ignore_patterns"])
        if "ai_ignore_strict" in patch:
            if not isinstance(patch["ai_ignore_strict"], bool):
                raise ValueError("ai_ignore_strict must be a boolean")
            settings.ai_ignore_strict = patch["ai_ignore_strict"]
        settings.validate()
        settings.save()
    from backend.bridge.shared_mcp import bump_catalog_revision

    bump_catalog_revision()  # strict protection changes which tools are listed
    return "Saved AI ignore list."



def normalize_rules(value: object) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(p, str) for p in value):
        raise ValueError("AI ignore rules must be a list of paths or patterns.")
    if len(value) > 500:
        raise ValueError("At most 500 AI ignore rules are allowed.")
    result: list[str] = []
    for raw in value:
        rule = raw.strip().replace("\\", "/")
        if not rule or rule.startswith("#"):
            continue
        if len(rule) > 2048 or "\0" in rule or "\n" in rule or "\r" in rule:
            raise ValueError("Invalid AI ignore rule.")
        if rule.startswith("!"):
            raise ValueError("AI ignore rules cannot use ! to allow access.")
        if ".." in rule.split("/"):
            raise ValueError("AI ignore rules cannot contain parent traversal.")
        if rule not in result:
            result.append(rule)
    return result


def _normal(path: str) -> str:
    # Windows names are case insensitive; trailing dots/spaces alias the same name.
    return "/".join(p.rstrip(" .").casefold() for p in path.replace("\\", "/").split("/"))


def _matches(path: str, rule: str) -> bool:
    path, rule = _normal(path), _normal(rule).rstrip("/")
    parts = path.split("/")
    prefixes = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    if "/" not in rule:
        return any(fnmatch.fnmatchcase(p, rule) for p in parts)
    variants = {rule, rule.replace("/**/", "/")}
    if rule.startswith("**/"):
        variants.add(rule[3:])
    return any(fnmatch.fnmatchcase(p, v) for p in prefixes for v in variants)


def _inside(path: str, root: str) -> str | None:
    """``path`` relative to ``root`` when it is inside it, else None. Both are already absolute
    and normalized, so a prefix check does what os.path.relpath did at a fraction of the cost."""
    folded, base = os.path.normcase(path), os.path.normcase(root).rstrip("\\/")
    if folded == base:
        return "."
    if folded.startswith(base + os.sep):
        return path[len(base) + 1:]
    return None


@dataclass(frozen=True)
class IgnorePolicy:
    rules: tuple[str, ...]
    roots: tuple[str, ...] = ()
    strict: bool = False

    @functools.cached_property
    def _real_roots(self) -> tuple[str, ...]:
        # Resolved once per policy: every check used to resolve every known project again
        # (two file-system calls each), so opening a chat with 3 pictures took 3 s.
        return tuple(dict.fromkeys(os.path.realpath(root) for root in self.roots if root))

    @functools.cached_property
    def _real_root_of(self) -> dict[str, str]:
        return {root: os.path.realpath(os.path.abspath(root)) for root in self.roots if root}

    def _named(self, lexical: str, canonical: str) -> bool:
        """A rule names the path, by itself or relative to a project."""
        candidates = {lexical, canonical}
        for root in self._real_roots:
            for candidate in (lexical, canonical):
                rel = _inside(candidate, root)
                if rel is not None:
                    candidates.add(rel)
        # ADS syntax can alias a protected file (e.g. .env::$DATA).
        for candidate in tuple(candidates):
            _drive, tail = ntpath.splitdrive(candidate)
            if ":" in tail:
                return True
        return any(_matches(candidate, rule) for candidate in candidates for rule in self.rules)

    def denied(self, path: str) -> bool:
        lexical = os.path.abspath(os.fspath(path))
        canonical = os.path.realpath(lexical)
        if self._named(lexical, canonical):
            return True
        # A hard link can hide the protected name. Refuse multi-linked files
        # instead of scanning and reading every filename on a volume.
        try:
            if os.path.isfile(canonical) and os.stat(canonical).st_nlink > 1:
                return True
        except OSError:
            return True
        return False

    def denied_hint(self, path: str) -> bool:
        """Attachment paths may be project-relative rather than process-relative."""
        if self.denied(path):
            return True
        if os.path.isabs(path):
            return False
        bare = "/" not in path and "\\" not in path
        for root in self.roots:
            joined = os.path.join(root, path)
            if bare and root in self._real_root_of and not os.path.lexists(joined):
                # Nothing there: no link to follow and none to count, and it resolves to the
                # project's real folder plus the name (resolving it again for each of 50+
                # known projects took 30 ms per attachment name).
                if self._named(os.path.abspath(joined), os.path.join(self._real_root_of[root], path)):
                    return True
            elif self.denied(joined):
                return True
        return False

    def require(self, path: str) -> None:
        if self.denied(path):
            raise ValueError(DENIED)


def current_policy() -> IgnorePolicy:
    # Lazy imports avoid settings/bridge cycles. Failure must never allow access.
    try:
        from frontend.settings import PanelSettings
        s = PanelSettings.load(fail_closed=True)
        rules = normalize_rules(s.ai_ignore_patterns)
        roots = [s.uefn_project_root] if s.uefn_project_root else []
        from backend.bridge.client import workspace_roots, _recent_project_roots
        roots.extend(workspace_roots())
        roots.extend(_recent_project_roots())
        # Protect the policy's storage and installed code from direct AI edits.
        from frontend.settings import default_app_data_dir
        rules.extend([
            str(default_app_data_dir() / "panel_settings.json").replace("\\", "/"),
            str(default_app_data_dir() / "ducky.db").replace("\\", "/") + "*",
        ])
        return _policy((*BUILTIN_RULES, *rules), tuple(roots), bool(s.ai_ignore_strict))
    except Exception as exc:
        raise ValueError(POLICY_UNAVAILABLE) from exc


@functools.lru_cache(maxsize=8)
def _policy(rules: tuple[str, ...], roots: tuple[str, ...], strict: bool) -> IgnorePolicy:
    """One policy per set of rules and roots, so its resolved roots are worked out once.
    A chat with pictures built a new one per attachment: every known project resolved again
    on each load (Oct 31 2026: the top idle-CPU stack)."""
    return IgnorePolicy(rules, roots, strict)


# Exact audited tools only. Unknown/plugin tools, wrappers, code execution,
# browser/desktop access and Git cannot bypass the guards in strict mode.
SAFE_TOOLS = frozenset({
    "workspace_read_file", "workspace_read_files", "workspace_file_outline", "view_image",
    "workspace_list_dir", "workspace_tree", "workspace_search", "workspace_find",
    "workspace_write_file", "workspace_edit_file", "workspace_multi_edit",
    "workspace_replace_lines", "workspace_move_file", "workspace_delete_file",
    "ducky_ask_user", "ducky_rename_self", "ducky_create_plan", "ducky_plan_update_node",
})


def require_safe_tool(name: str) -> None:
    if current_policy().strict and name not in SAFE_TOOLS:
        raise ValueError(
            "AI_FILE_PROTECTION: this tool is blocked in strict protection mode. "
            "Use guarded workspace tools; only the user can change protection."
        )


def require_ai_mutation(path: str) -> None:
    policy = current_policy()
    policy.require(path)
    from frontend.settings import panel_exe_dir
    installed = str(panel_exe_dir().resolve())
    full = os.path.realpath(os.path.abspath(path))
    from frontend.settings import default_app_data_dir
    for protected_root in (installed, str(default_app_data_dir().resolve())):
        try:
            inside = os.path.normcase(os.path.commonpath([full, protected_root])) == os.path.normcase(protected_root)
        except ValueError:
            inside = False
        if policy.strict and inside:
            raise ValueError("AI_FILE_PROTECTION: AI cannot modify its installed enforcement code or app data.")


def require_ai_path_operation(source: str, destination: str | None = None) -> None:
    """Reject whole-folder operations touching a denied descendant, before mutation."""
    require_ai_mutation(source)
    if destination:
        require_ai_mutation(destination)
    def fail(exc: OSError) -> None:
        raise ValueError(POLICY_UNAVAILABLE) from exc
    if os.path.isdir(source):
        for folder, dirs, files in os.walk(source, followlinks=False, onerror=fail):
            for name in dirs + files:
                full = os.path.join(folder, name)
                require_ai_mutation(full)
                if os.path.isdir(full) and (os.path.islink(full) or getattr(os.path, "isjunction", lambda _: False)(full)):
                    raise ValueError(DENIED)
                if destination:
                    require_ai_mutation(os.path.join(destination, os.path.relpath(full, source)))


def require_ai_access(path: str) -> None:
    current_policy().require(path)


def ai_access_allowed(path: str, *, policy: IgnorePolicy | None = None) -> bool:
    return not (policy or current_policy()).denied(path)
