"""User-owned AI file denials. Checked before reading bytes or modifying disk.

These guards protect Ducky-mediated operations, not an arbitrary process running
as the user's Windows account. Unsandboxed execution must be refused separately.
No negation rules: an AI must never be able to unignore a protected path.
"""
from __future__ import annotations

import fnmatch
import ntpath
import os
import threading
from dataclasses import dataclass

BUILTIN_RULES = (".env", ".env.*")
DENIED = "AI_FILE_IGNORED: access denied by the user's AI ignore list."
POLICY_UNAVAILABLE = "AI_FILE_POLICY_UNAVAILABLE: access refused."
protection_lock = threading.RLock()


def save_user_policy(patch: dict) -> str:
    """UI-only save; do not activate a changed policy over a running agent."""
    from frontend.settings import PanelSettings
    from frontend.ui_web.live_agent_runs import get_live_run_ids
    with protection_lock:
        if get_live_run_ids():
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


@dataclass(frozen=True)
class IgnorePolicy:
    rules: tuple[str, ...]
    roots: tuple[str, ...] = ()
    strict: bool = False

    def denied(self, path: str) -> bool:
        lexical = os.path.abspath(os.fspath(path))
        canonical = os.path.realpath(lexical)
        candidates = {lexical, canonical}
        for root in self.roots:
            for candidate in (lexical, canonical):
                try:
                    rel = os.path.relpath(candidate, os.path.realpath(root))
                except ValueError:
                    continue
                if rel != ".." and not rel.startswith(".." + os.sep):
                    candidates.add(rel)
        # ADS syntax can alias a protected file (e.g. .env::$DATA).
        for candidate in tuple(candidates):
            _drive, tail = ntpath.splitdrive(candidate)
            if ":" in tail:
                return True
        if any(_matches(candidate, rule) for candidate in candidates for rule in self.rules):
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
        return self.denied(path) or (not os.path.isabs(path) and any(
            self.denied(os.path.join(root, path)) for root in self.roots
        ))

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
        return IgnorePolicy((*BUILTIN_RULES, *rules), tuple(roots), bool(s.ai_ignore_strict))
    except Exception as exc:
        raise ValueError(POLICY_UNAVAILABLE) from exc


# Exact audited tools only. Unknown/plugin tools, wrappers, code execution,
# browser/desktop access and Git cannot bypass the guards in strict mode.
SAFE_TOOLS = frozenset({
    "workspace_read_file", "workspace_read_files", "workspace_file_outline",
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
