"""CLI-based multi-language diagnostics (no language servers)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

# tsc: file(line,col): error TSxxxx: message
_TSC_RE = re.compile(
    r"^(?P<path>.+?)\((?P<line>\d+),(?P<col>\d+)\):\s+"
    r"(?P<sev>error|warning)\s+(?P<code>TS\d+):\s+(?P<msg>.+)$"
)
# eslint stylish-ish / unix: path:line:col: message [Error/Warning]
_ESLINT_UNIX_RE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+):(?P<col>\d+):\s+"
    r"(?P<msg>.+?)\s+\[(?P<sev>Error|Warning)/(?P<code>[^\]]+)\]\s*$"
)
# php -l: PHP Parse error: ... in file on line N
_PHP_L_RE = re.compile(
    r"^(?:Parse|Fatal) error:\s*(?P<msg>.+?)\s+in\s+(?P<path>.+?)\s+on line\s+(?P<line>\d+)",
    re.IGNORECASE,
)
# clang/gcc: file:line:col: error: message
_CLANG_RE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+):(?P<col>\d+):\s+"
    r"(?P<sev>error|warning|note):\s+(?P<msg>.+)$"
)

Diagnostic = dict[str, Any]


def _which(name: str) -> str | None:
    return shutil.which(name)


def _run(
    argv: list[str],
    *,
    cwd: str,
    timeout: float = 120.0,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        shell=False,
    )


def _diag(
    path: str,
    line: int,
    column: int,
    message: str,
    *,
    severity: str = "error",
    source: str = "",
) -> Diagnostic:
    return {
        "path": path.replace("\\", "/"),
        "line": int(line),
        "column": int(column),
        "message": message.strip(),
        "severity": severity.lower(),
        "source": source,
    }


def parse_tsc(stdout: str, stderr: str = "") -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for line in (stdout + "\n" + stderr).splitlines():
        m = _TSC_RE.match(line.strip())
        if not m:
            continue
        out.append(
            _diag(
                m.group("path"),
                int(m.group("line")),
                int(m.group("col")),
                f"{m.group('code')}: {m.group('msg')}",
                severity=m.group("sev"),
                source="tsc",
            )
        )
    return out


def parse_eslint_json(text: str) -> list[Diagnostic]:
    try:
        data = json.loads(text or "[]")
    except json.JSONDecodeError:
        return []
    out: list[Diagnostic] = []
    if not isinstance(data, list):
        return out
    for file_res in data:
        if not isinstance(file_res, dict):
            continue
        fpath = str(file_res.get("filePath") or "")
        for msg in file_res.get("messages") or []:
            if not isinstance(msg, dict):
                continue
            sev = "error" if int(msg.get("severity") or 0) >= 2 else "warning"
            rule = str(msg.get("ruleId") or "eslint")
            out.append(
                _diag(
                    fpath,
                    int(msg.get("line") or 1),
                    int(msg.get("column") or 1),
                    f"{rule}: {msg.get('message') or ''}",
                    severity=sev,
                    source="eslint",
                )
            )
    return out


def parse_eslint_unix(text: str) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for line in (text or "").splitlines():
        m = _ESLINT_UNIX_RE.match(line.strip())
        if not m:
            continue
        out.append(
            _diag(
                m.group("path"),
                int(m.group("line")),
                int(m.group("col")),
                f"{m.group('code')}: {m.group('msg')}",
                severity=m.group("sev"),
                source="eslint",
            )
        )
    return out


def parse_cargo_json(stdout: str) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("reason") != "compiler-message":
            continue
        inner = msg.get("message") or {}
        if not isinstance(inner, dict):
            continue
        level = str(inner.get("level") or "error")
        if level not in ("error", "warning"):
            continue
        spans = inner.get("spans") or []
        primary = next((s for s in spans if isinstance(s, dict) and s.get("is_primary")), None)
        if not isinstance(primary, dict):
            primary = spans[0] if spans and isinstance(spans[0], dict) else {}
        fpath = str(primary.get("file_name") or "")
        out.append(
            _diag(
                fpath,
                int(primary.get("line_start") or 1),
                int(primary.get("column_start") or 1),
                str(inner.get("message") or ""),
                severity=level,
                source="cargo",
            )
        )
    return out


def parse_php_l(text: str, fallback_path: str = "") -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for line in (text or "").splitlines():
        m = _PHP_L_RE.search(line.strip())
        if not m:
            continue
        out.append(
            _diag(
                m.group("path") or fallback_path,
                int(m.group("line")),
                1,
                m.group("msg"),
                severity="error",
                source="php",
            )
        )
    return out


def parse_clang(text: str) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for line in (text or "").splitlines():
        m = _CLANG_RE.match(line.strip())
        if not m:
            continue
        sev = m.group("sev")
        if sev == "note":
            continue
        out.append(
            _diag(
                m.group("path"),
                int(m.group("line")),
                int(m.group("col")),
                m.group("msg"),
                severity=sev,
                source="clang",
            )
        )
    return out


def _workspace_roots() -> list[str]:
    try:
        from backend.bridge import workspace_roots

        return list(workspace_roots() or [])
    except Exception:  # noqa: BLE001
        return []


def _resolve_path(path: str | None) -> Path | None:
    if not path or not str(path).strip():
        roots = _workspace_roots()
        return Path(roots[0]) if roots else None
    raw = str(path).strip()
    try:
        from backend.bridge import resolve_workspace_path

        return Path(resolve_workspace_path(raw))
    except Exception:  # noqa: BLE001
        p = Path(raw)
        return p if p.exists() else None


def _find_marker(start: Path, names: tuple[str, ...]) -> Path | None:
    cur = start if start.is_dir() else start.parent
    for _ in range(24):
        for name in names:
            cand = cur / name
            if cand.is_file() or (name.endswith("/") and cand.is_dir()):
                return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def detect_project(path: str | None = None) -> dict[str, Any]:
    target = _resolve_path(path)
    roots = _workspace_roots()
    base = target if target and target.is_dir() else (target.parent if target else None)
    if base is None and roots:
        base = Path(roots[0])

    kinds: list[str] = []
    markers: dict[str, str] = {}
    if base is not None:
        node = _find_marker(base, ("package.json", "tsconfig.json"))
        if node:
            kinds.append("node")
            for name in ("package.json", "tsconfig.json", "eslint.config.js", "eslint.config.mjs", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json"):
                p = node / name
                if p.is_file():
                    markers[name] = str(p)
        rust = _find_marker(base, ("Cargo.toml",))
        if rust:
            kinds.append("rust")
            markers["Cargo.toml"] = str(rust / "Cargo.toml")
        php = _find_marker(base, ("composer.json",))
        if php:
            kinds.append("php")
            markers["composer.json"] = str(php / "composer.json")
        cpp = _find_marker(base, ("CMakeLists.txt", "compile_commands.json"))
        if cpp:
            kinds.append("cpp")
            for name in ("CMakeLists.txt", "compile_commands.json"):
                p = cpp / name
                if p.is_file():
                    markers[name] = str(p)

    toolchains = {
        "node": bool(_which("node")),
        "npm": bool(_which("npm")),
        "npx": bool(_which("npx")),
        "tsc": bool(_which("tsc")),
        "cargo": bool(_which("cargo")),
        "rustc": bool(_which("rustc")),
        "php": bool(_which("php")),
        "clang++": bool(_which("clang++")),
        "clang": bool(_which("clang")),
        "g++": bool(_which("g++")),
    }
    return {
        "ok": True,
        "path": str(base) if base else None,
        "kinds": kinds,
        "markers": markers,
        "toolchains": toolchains,
    }


def _language_from_path(p: Path) -> str | None:
    suf = p.suffix.lower()
    return {
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".rs": "rust",
        ".php": "php",
        ".c": "c",
        ".h": "c",
        ".cpp": "cpp",
        ".cxx": "cpp",
        ".cc": "cpp",
        ".hpp": "cpp",
        ".hh": "cpp",
    }.get(suf)


def list_errors(path: str | None = None, language: str | None = None) -> dict[str, Any]:
    target = _resolve_path(path)
    if target is None:
        return {"ok": False, "error": "No workspace root or path. Set the project or pass path=."}

    lang = (language or "").strip().lower() or None
    if not lang and target.is_file():
        lang = _language_from_path(target)

    info = detect_project(str(target))
    kinds = list(info.get("kinds") or [])
    if lang in ("typescript", "javascript", "ts", "js"):
        kinds = ["node"]
        lang = "typescript" if lang in ("typescript", "ts", None) else "javascript"
    elif lang == "rust":
        kinds = ["rust"]
    elif lang == "php":
        kinds = ["php"]
    elif lang in ("cpp", "c", "c++"):
        kinds = ["cpp"]

    if not kinds:
        # Infer from extension when no markers
        if lang:
            pass
        elif target.is_file() and target.suffix.lower() == ".php":
            kinds = ["php"]
        elif target.is_file() and target.suffix.lower() in {".c", ".h", ".cpp", ".cxx", ".cc", ".hpp", ".hh"}:
            kinds = ["cpp"]
        else:
            return {
                "ok": False,
                "error": "Could not detect project kind. Pass language=typescript|rust|php|cpp or open a folder with package.json / Cargo.toml / composer.json / CMakeLists.txt.",
                "detect": info,
            }

    errors: list[Diagnostic] = []
    ran: list[str] = []
    missing: list[str] = []

    if "node" in kinds:
        node_root = _find_marker(target, ("package.json", "tsconfig.json")) or (
            target if target.is_dir() else target.parent
        )
        ts_errs, ts_ran, ts_miss = _run_typescript(node_root, target if target.is_file() else None)
        errors.extend(ts_errs)
        ran.extend(ts_ran)
        missing.extend(ts_miss)

    if "rust" in kinds:
        rust_root = _find_marker(target, ("Cargo.toml",))
        if not rust_root:
            missing.append("Cargo.toml")
        elif not _which("cargo"):
            missing.append("cargo (not on PATH)")
        else:
            ran.append("cargo check")
            try:
                proc = _run(
                    ["cargo", "check", "--message-format=json"],
                    cwd=str(rust_root),
                    timeout=180.0,
                )
                errors.extend(parse_cargo_json(proc.stdout))
            except subprocess.TimeoutExpired:
                return {"ok": False, "error": "cargo check timed out", "ran": ran}
            except FileNotFoundError:
                missing.append("cargo (not on PATH)")

    if "php" in kinds:
        php_errs, php_ran, php_miss = _run_php(target)
        errors.extend(php_errs)
        ran.extend(php_ran)
        missing.extend(php_miss)

    if "cpp" in kinds:
        cpp_errs, cpp_ran, cpp_miss = _run_cpp(target)
        errors.extend(cpp_errs)
        ran.extend(cpp_ran)
        missing.extend(cpp_miss)

    if not ran and missing:
        return {
            "ok": False,
            "error": "Toolchain missing: " + ", ".join(missing),
            "missing": missing,
            "detect": info,
            "errors": [],
        }

    return {
        "ok": True,
        "errors": errors,
        "count": len(errors),
        "ran": ran,
        "missing": missing,
        "detect": info,
    }


def _run_typescript(root: Path, file: Path | None) -> tuple[list[Diagnostic], list[str], list[str]]:
    errors: list[Diagnostic] = []
    ran: list[str] = []
    missing: list[str] = []
    npx = _which("npx")
    tsc_bin = _which("tsc")
    has_tsconfig = (root / "tsconfig.json").is_file()

    if has_tsconfig or (file and file.suffix.lower() in {".ts", ".tsx"}):
        argv: list[str] | None = None
        if tsc_bin:
            argv = [tsc_bin, "--noEmit", "--pretty", "false"]
        elif npx:
            argv = [npx, "--yes", "typescript", "tsc", "--noEmit", "--pretty", "false"]
        else:
            missing.append("tsc/npx (not on PATH)")
        if argv:
            ran.append("tsc --noEmit")
            try:
                proc = _run(argv, cwd=str(root), timeout=120.0)
                errors.extend(parse_tsc(proc.stdout, proc.stderr))
            except subprocess.TimeoutExpired:
                missing.append("tsc timed out")
            except FileNotFoundError:
                missing.append("tsc/npx (not on PATH)")

    eslint_cfg = any(
        (root / name).is_file()
        for name in (
            "eslint.config.js",
            "eslint.config.mjs",
            "eslint.config.cjs",
            ".eslintrc.js",
            ".eslintrc.cjs",
            ".eslintrc.json",
            ".eslintrc.yml",
            ".eslintrc.yaml",
        )
    )
    if eslint_cfg:
        if not npx and not _which("eslint"):
            missing.append("eslint/npx (not on PATH)")
        else:
            eslint_argv = (
                [_which("eslint") or "eslint", "-f", "json", "."]
                if _which("eslint")
                else [npx, "--yes", "eslint", "-f", "json", "."]  # type: ignore[list-item]
            )
            ran.append("eslint")
            try:
                proc = _run(eslint_argv, cwd=str(root), timeout=120.0)  # type: ignore[arg-type]
                parsed = parse_eslint_json(proc.stdout)
                if not parsed:
                    parsed = parse_eslint_unix(proc.stdout + "\n" + proc.stderr)
                errors.extend(parsed)
            except subprocess.TimeoutExpired:
                missing.append("eslint timed out")
            except FileNotFoundError:
                missing.append("eslint/npx (not on PATH)")

    return errors, ran, missing


def _run_php(target: Path) -> tuple[list[Diagnostic], list[str], list[str]]:
    errors: list[Diagnostic] = []
    ran: list[str] = []
    missing: list[str] = []
    php = _which("php")
    if not php:
        return [], [], ["php (not on PATH)"]

    files: list[Path] = []
    if target.is_file() and target.suffix.lower() == ".php":
        files = [target]
    elif target.is_dir():
        files = list(target.rglob("*.php"))[:40]  # ponytail: cap scan; use phpstan later
    else:
        # marker root — lint a few php files under it
        root = _find_marker(target, ("composer.json",)) or target.parent
        files = list(root.rglob("*.php"))[:40]

    if not files:
        return [], [], ["no .php files found"]

    for f in files:
        ran.append(f"php -l {f.name}")
        try:
            proc = _run([php, "-l", str(f)], cwd=str(f.parent), timeout=30.0)
            text = (proc.stdout or "") + "\n" + (proc.stderr or "")
            errors.extend(parse_php_l(text, fallback_path=str(f)))
        except (subprocess.TimeoutExpired, FileNotFoundError):
            missing.append(f"php -l failed for {f}")
            break
    # de-dupe ran labels
    ran = ["php -l"] if ran else []
    return errors, ran, missing


def _run_cpp(target: Path) -> tuple[list[Diagnostic], list[str], list[str]]:
    errors: list[Diagnostic] = []
    ran: list[str] = []
    missing: list[str] = []
    compiler = _which("clang++") or _which("clang") or _which("g++")
    if not compiler:
        return [], [], ["clang++/clang/g++ (not on PATH)"]

    files: list[Path] = []
    if target.is_file() and target.suffix.lower() in {".c", ".h", ".cpp", ".cxx", ".cc", ".hpp", ".hh"}:
        files = [target]
    else:
        root = _find_marker(target, ("CMakeLists.txt", "compile_commands.json")) or (
            target if target.is_dir() else target.parent
        )
        for pat in ("*.cpp", "*.cc", "*.cxx", "*.c"):
            files.extend(list(root.rglob(pat))[:10])
            if files:
                break

    if not files:
        return [], [], ["no C/C++ source files found"]

    for f in files[:8]:
        argv = [compiler, "-fsyntax-only", "-Wall", str(f)]
        ran.append(f"{Path(compiler).name} -fsyntax-only")
        try:
            proc = _run(argv, cwd=str(f.parent), timeout=60.0)
            errors.extend(parse_clang((proc.stderr or "") + "\n" + (proc.stdout or "")))
        except subprocess.TimeoutExpired:
            missing.append("clang timed out")
            break
        except FileNotFoundError:
            missing.append("clang++/clang/g++ (not on PATH)")
            break
    ran = list(dict.fromkeys(ran))
    return errors, ran, missing


def open_file(path: str, line: int = 1, column: int = 1) -> dict[str, Any]:
    rel = (path or "").strip().replace("\\", "/")
    if not rel:
        return {"ok": False, "error": "path is required"}
    ln = max(1, int(line))
    col = max(1, int(column))
    try:
        from backend.bridge import resolve_workspace_path

        absolute = resolve_workspace_path(rel)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    try:
        from frontend.ui_web.verse_editor.feature_flag import verse_editor_enabled
        from frontend.ui_web.verse_editor.panel_events import push_agent_event
        from frontend.ui_web.verse_editor.types import EditorAction, EditorBatch, EditorRange

        if not verse_editor_enabled():
            return {"ok": False, "error": "Editor is disabled", "path": rel, "absolute": absolute}

        batch = EditorBatch(
            actions=[
                EditorAction(type="open_file", path=rel, activate=False),
                EditorAction(
                    type="scroll_to",
                    path=rel,
                    range=EditorRange(ln, col, ln, col),
                    duration_ms=200,
                ),
                EditorAction(
                    type="highlight",
                    path=rel,
                    range=EditorRange(ln, col, ln, col),
                    style="agent_cursor",
                    duration_ms=600,
                ),
                EditorAction(type="clear_decorations", path=rel),
            ],
        )
        push_agent_event({"type": "editor_batch", "editor_batch": batch.to_dict()})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"open failed: {exc}", "path": rel, "absolute": absolute}

    return {"ok": True, "path": rel, "absolute": absolute, "line": ln, "column": col}
