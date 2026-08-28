"""Window chrome, focus windows, browser panes, workspace dock. Mixin for PanelApi — methods stay on the PyWebView JS object."""

from __future__ import annotations

from typing import Any

import frontend.ui_web.panel_api as _pa


class PanelApiWindowMixin:
    def bind_tk_root(self, root: Any) -> None:
        self._tk_root = root

    def bind_window(self, window: Any, *, on_hide: Any, on_exit: Any) -> None:
        self._window = window
        self._hide_callback = on_hide
        self._exit_callback = on_exit
        from frontend.ui_web.agent_modes import set_panel_push

        set_panel_push(self._push)
        from frontend.ui_web.terminal import get_terminal_manager

        get_terminal_manager().set_push(self._push)
        self._flush_pending_panel_pushes()

    def _resolve_window(self) -> Any | None:
        try:
            import webview

            active = webview.active_window()
            if active is not None:
                return active
        except Exception:
            pass
        return self._window

    def _all_windows(self) -> list[Any]:
        try:
            from frontend.ui_web import focus_windows

            return focus_windows.all_windows()
        except Exception:
            return [self._window] if self._window else []

    def _evaluate_all(self, js: str) -> None:
        from frontend.ui_web.ui_dispatch import schedule_evaluate_js

        for w in self._all_windows():
            if w is None:
                continue
            schedule_evaluate_js(w, js)

    def get_window_bounds(self) -> dict[str, int | float]:
        w = self._resolve_window()
        if not w:
            return {"x": 0, "y": 0, "width": 920, "height": 640, "scale": 1.0}
        scale = 1.0
        if _pa.sys.platform == "win32":
            from frontend.ui_web.win_frameless import get_window_scale

            scale = get_window_scale(w)
        return {
            "x": int(w.x),
            "y": int(w.y),
            "width": int(w.width),
            "height": int(w.height),
            "scale": scale,
        }

    def set_window_bounds(self, x: int, y: int, width: int, height: int) -> None:
        w = self._resolve_window()
        if not w:
            return

        def apply() -> None:
            from frontend.ui_web import focus_windows
            from frontend.ui_web.window_layout import (
                MAIN_MIN_HEIGHT,
                MAIN_MIN_WIDTH,
                SIDEBAR_ONLY_MIN_WIDTH,
            )

            is_focus = focus_windows.is_focus_window(w)
            if is_focus:
                min_w, min_h = 360, SIDEBAR_ONLY_MIN_WIDTH
            elif self._sidebar_only_layout:
                min_w, min_h = SIDEBAR_ONLY_MIN_WIDTH, SIDEBAR_ONLY_MIN_WIDTH
            else:
                min_w, min_h = MAIN_MIN_WIDTH, MAIN_MIN_HEIGHT
            width_clamped = max(min_w, int(width))
            height_clamped = max(min_h, int(height))
            x_pos, y_pos = int(x), int(y)
            if _pa.sys.platform == "win32":
                from frontend.ui_web.win_frameless import set_window_bounds_hwnd

                if set_window_bounds_hwnd(w, x_pos, y_pos, width_clamped, height_clamped):
                    try:
                        w.x = x_pos
                        w.y = y_pos
                        w.width = width_clamped
                        w.height = height_clamped
                    except Exception:
                        pass
                    self._maximized = False
                    return
            w.move(x_pos, y_pos)
            w.resize(width_clamped, height_clamped)
            self._maximized = False

        native = getattr(w, "native", None)
        if native is not None and _pa.sys.platform == "win32":
            from frontend.ui_web.win_frameless import _run_on_form_ui

            _run_on_form_ui(native, apply)
        else:
            apply()

    def set_sidebar_only_layout(self, enabled: bool) -> None:
        """Toggle compact sidebar-only min size + native resize limits."""
        self._sidebar_only_layout = bool(enabled)
        w = self._resolve_window()
        if not w:
            return
        from frontend.ui_web.win_frameless import enter_sidebar_only_layout, exit_sidebar_only_layout

        if enabled:
            saved = enter_sidebar_only_layout(w)
            if saved:
                self._sidebar_only_saved_bounds = saved
        else:
            self._sidebar_only_saved_bounds = None
            exit_sidebar_only_layout(w)

    def get_sidebar_only_saved_bounds(self) -> dict[str, int | float] | None:
        return self._sidebar_only_saved_bounds

    def dock_focus_window_beside_main(self) -> None:
        from frontend.ui_web import focus_windows

        focus_windows.dock_focus_window_beside_main()

    def uses_native_window_chrome(self) -> bool:
        return _pa.sys.platform == "win32"

    def begin_native_window_move(self) -> bool:
        w = self._resolve_window()
        if not w or _pa.sys.platform != "win32":
            return False
        from frontend.ui_web.win_frameless import begin_native_window_move

        return begin_native_window_move(w)

    def begin_native_window_resize(self, edge: str) -> bool:
        w = self._resolve_window()
        if not w or _pa.sys.platform != "win32":
            return False
        from frontend.ui_web.win_frameless import begin_native_window_resize

        return begin_native_window_resize(w, edge)

    def _read_maximized(self, w: Any) -> bool:
        """Prefer the real OS zoom state so native double-click maximise stays in sync."""
        if _pa.sys.platform == "win32":
            from frontend.ui_web.win_frameless import is_window_maximized

            return is_window_maximized(w)
        return self._maximized

    def is_window_maximized(self) -> bool:
        w = self._resolve_window()
        if not w:
            return False
        return self._read_maximized(w)

    def toggle_maximize(self) -> bool:
        w = self._resolve_window()
        if not w:
            return False
        # Decide from the actual window state, not a cached flag: the OS maximises the
        # window on a native caption double-click without routing through here, so a cached
        # bool drifts and the first button press becomes a wasted no-op.
        if self._read_maximized(w):
            w.restore()
            self._maximized = False
        else:
            w.maximize()
            self._maximized = True
        return self._read_maximized(w)

    def minimize_window(self) -> None:
        w = self._resolve_window()
        if w:
            w.minimize()

    def open_focus_window(self, focus_id: str, title: str, solo: bool = False) -> None:
        from frontend.ui_web import focus_windows

        focus_windows.open_focus_window(focus_id, title, solo=bool(solo))

    def open_focus_window_group(self, tabs: list | None = None) -> None:
        """Move a batch of tabs into ONE focus window (sidebar-only hand-off)."""
        from frontend.ui_web import focus_windows

        focus_windows.open_focus_window_group(list(tabs or []))

    def list_focus_tab_ids(self) -> list[str]:
        """Tab ids currently hosted by any focus window."""
        from frontend.ui_web import focus_windows

        return focus_windows.list_focus_window_ids()

    def open_focus_window_at_point(self, focus_id: str, title: str, screen_x: int, screen_y: int) -> bool:
        """False when the drop landed back in the main window — caller keeps the tab."""
        from frontend.ui_web import focus_windows

        return focus_windows.open_focus_window_at_point(focus_id, title, int(screen_x), int(screen_y))

    def adopt_tab_into_this_focus_window(self, focus_id: str, title: str) -> None:
        from frontend.ui_web import focus_windows

        w = self._resolve_window()
        if w is None:
            raise RuntimeError("no window")
        focus_windows.adopt_tab_into_focus_window(focus_id, title, w)

    def raise_focus_window(self, focus_id: str) -> None:
        from frontend.ui_web import focus_windows

        focus_windows.raise_focus_window(focus_id)

    # ── Browser panes (native WebView2 pinned inside a window; plugin web panes) ──

    def browser_pane_open(self, pane_id: str, url: str = "", wid: str = "") -> dict[str, Any]:
        from frontend.ui_web import browser_overlay

        return browser_overlay.open_pane(str(pane_id or ""), str(url or ""), str(wid or ""))

    def browser_pane_set_bounds(
        self,
        pane_id: str,
        x: float,
        y: float,
        width: float,
        height: float,
        viewport_w: float = 0.0,
        viewport_h: float = 0.0,
        visible: bool = True,
    ) -> dict[str, Any]:
        from frontend.ui_web import browser_overlay

        return browser_overlay.set_bounds(
            str(pane_id or ""), float(x), float(y), float(width), float(height),
            float(viewport_w or 0.0), float(viewport_h or 0.0), bool(visible),
        )

    def browser_pane_navigate(self, pane_id: str, url: str) -> dict[str, Any]:
        from frontend.ui_web import browser_overlay

        return browser_overlay.navigate(str(pane_id or ""), str(url or ""))

    def browser_pane_command(self, pane_id: str, command: str) -> dict[str, Any]:
        from frontend.ui_web import browser_overlay

        return browser_overlay.command(str(pane_id or ""), str(command or ""))

    def browser_pane_state(self, pane_id: str) -> dict[str, Any]:
        from frontend.ui_web import browser_overlay

        return browser_overlay.get_state(str(pane_id or ""))

    def browser_pane_close(self, pane_id: str) -> dict[str, Any]:
        from frontend.ui_web import browser_overlay

        return browser_overlay.close_pane(str(pane_id or ""))

    def browser_pane_list(self) -> dict[str, Any]:
        from frontend.ui_web import browser_overlay

        panes = browser_overlay.list_panes()
        return {"ok": True, "panes": panes, "pane_count": len(panes)}

    def browser_clear_browsing_data(self, kinds: str = "all") -> dict[str, Any]:
        from frontend.ui_web import browser_overlay

        return browser_overlay.clear_browsing_data(str(kinds or "all"))

    def browser_runtime_info(self) -> dict[str, Any]:
        from frontend.ui_web import browser_overlay

        return browser_overlay.runtime_info()

    def browser_site_security(self, pane_id: str = "") -> dict[str, Any]:
        from frontend.ui_web import browser_overlay

        # Must stay sync+fast: pywebview may invoke this on the UI thread.
        # Cookie waits are skipped on that thread inside site_security_info.
        return browser_overlay.site_security_info(str(pane_id or ""))

    def browser_pane_hide_all(self) -> dict[str, Any]:
        from frontend.ui_web import browser_overlay

        return browser_overlay.hide_all_panes()

    def report_open_tabs(self, window_id: str = "", tab_ids: list | None = None) -> None:
        from frontend.ui_web import tab_registry

        tab_registry.report_open_tabs(window_id or "main", list(tab_ids or []))

    def focus_tab(self, tab_id: str, requesting_window: str = "") -> dict[str, object]:
        """VS Code single-tab rule: raise + activate the window that owns tab_id.

        Returns ok=False when no OTHER window owns it (caller opens locally + claims).
        """
        from frontend.ui_web import focus_windows, tab_registry

        owner = tab_registry.find_tab_owner(tab_id, exclude_window=requesting_window or "main")
        if not owner:
            return {"ok": False, "window_id": ""}
        try:
            if owner == "main":
                w = self._window
                if w is not None:
                    w.restore()
            else:
                # owner is an opaque wid (focus-<uuid>) — raise the OS window; the
                # tab itself activates via the tab_focus_request broadcast below.
                focus_windows.raise_window(owner)
        except Exception:
            pass
        self._push({"type": "tab_focus_request", "tab_id": tab_id, "window_id": owner})
        return {"ok": True, "window_id": owner}

    def claim_tab(self, tab_id: str, window_id: str = "") -> None:
        """Broadcast ownership; every other window holding tab_id closes its copy."""
        self._push({"type": "tab_claimed", "tab_id": tab_id, "window_id": window_id or "main"})

    def notify_focus_tab_active(self, focus_id: str, title: str) -> None:
        from frontend.ui_web import focus_windows

        focus_windows.notify_focus_tab_active(focus_id, title)

    def report_focus_window_layout(self, birth_tab_id: str, layout: dict) -> None:
        from frontend.ui_web import focus_windows

        focus_windows.report_focus_window_layout(birth_tab_id, layout if isinstance(layout, dict) else {})

    def return_tab_to_main(self, focus_id: str, title: str) -> bool:
        from frontend.ui_web import focus_windows

        return bool(focus_windows.return_tab_to_main(focus_id, title))

    def close_focus_window(self, focus_id: str) -> None:
        from frontend.ui_web import focus_windows

        focus_windows.close_focus_window(focus_id)

    def close_all_focus_windows(self) -> None:
        from frontend.ui_web import focus_windows

        focus_windows.close_all_focus_windows()

    def get_editor_workspace(self, slug: str = "") -> dict[str, Any]:
        from frontend.ui_web.editor_workspace import load_editor_workspace

        return load_editor_workspace(slug.strip() or None)

    def save_editor_workspace(self, payload: dict[str, Any]) -> None:
        from frontend.ui_web.editor_workspace import save_editor_workspace

        save_editor_workspace(payload)

    def get_workspace_dock(self, window_id: str = "main") -> dict[str, Any]:
        """Left/right rail layout (sides, widths, splits) — AppData durable store."""
        from frontend.ui_web.workspace_dock import load_window

        return load_window(window_id) or {}

    def save_workspace_dock(self, payload: dict[str, Any] | str | None) -> None:
        from frontend.ui_web.workspace_dock import save_window

        data = _pa._coerce_mapping(payload, label="workspace dock")
        window_id = str(data.get("window_id") or "main").strip() or "main"
        snapshot = data.get("snapshot")
        if not isinstance(snapshot, dict):
            snapshot = {k: v for k, v in data.items() if k != "window_id"}
        if not snapshot:
            return
        save_window(window_id, snapshot)

    def restore_focus_windows(self, groups: list | None = None) -> None:
        from frontend.ui_web import focus_windows

        focus_windows.restore_groups(list(groups or []))

    def report_editor_state(self, relative_path: str, state: dict[str, Any]) -> None:
        from frontend.ui_web.verse_editor.editor_state_registry import report_state

        report_state(relative_path, state)

    def close_this_window(self) -> None:
        from frontend.ui_web import focus_windows

        w = self._resolve_window()
        if w is None or w is self._window:
            return
        focus_windows.close_window(w)

    def is_focus_window(self) -> bool:
        from frontend.ui_web import focus_windows

        w = self._resolve_window()
        return w is not None and focus_windows.is_focus_window(w)

    def get_version(self) -> str:
        return _pa.__version__

    def get_app_update_status(self) -> dict[str, Any]:
        from frontend.version_check import get_app_update_status

        return get_app_update_status()

    def get_install_info(self) -> dict[str, Any]:
        from frontend.install_info import get_install_info

        return get_install_info()

    def apply_update(self) -> dict[str, Any]:
        from frontend.updater import apply_update

        return apply_update()

    def get_update_progress(self) -> dict[str, Any]:
        from frontend.updater import get_update_progress

        return get_update_progress()

    def cancel_update(self) -> dict[str, Any]:
        from frontend.updater import cancel_update

        return cancel_update()

    def launch_uninstall(self) -> dict[str, Any]:
        from frontend.updater import launch_uninstall

        return launch_uninstall()

    def open_download_page(self) -> None:
        import webbrowser

        from frontend.version_check import download_page_url

        webbrowser.open(download_page_url())

    def open_devtools(self) -> dict[str, Any]:
        """Open WebView2 DevTools (Inspector) even in production builds."""
        w = self._resolve_window()
        if w is None:
            return {"ok": False, "error": "no window"}
        try:
            native = getattr(w, "native", None)
            browser = getattr(native, "browser", None) or getattr(native, "webview", None)
            # WinForms Edge: native.webview is the WebView2 control; CoreWebView2 on it.
            ctrl = getattr(native, "webview", None) or getattr(browser, "webview", None) or browser
            core = getattr(ctrl, "CoreWebView2", None)
            if core is None:
                return {"ok": False, "error": "CoreWebView2 unavailable"}
            settings = getattr(core, "Settings", None)
            if settings is not None:
                try:
                    settings.AreDevToolsEnabled = True
                    settings.AreDefaultContextMenusEnabled = True
                    settings.AreBrowserAcceleratorKeysEnabled = True
                except Exception:
                    pass
            core.OpenDevToolsWindow()
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def report_ui_crash(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Append a UI ErrorBoundary crash to AppData for support / debugging."""
        import json
        import time

        data = payload if isinstance(payload, dict) else {}
        try:
            from frontend.app_paths import resolve_app_data_dir

            path = resolve_app_data_dir(for_write=True) / "ui_crashes.jsonl"
            row = {
                "ts": _pa.time.time(),
                "version": _pa.__version__,
                "label": str(data.get("label") or ""),
                "message": str(data.get("message") or "")[:2000],
                "stack": str(data.get("stack") or "")[:8000],
                "componentStack": str(data.get("componentStack") or "")[:8000],
                "appVersion": str(data.get("appVersion") or _pa.__version__),
                "pluginId": str(data.get("pluginId") or "")[:128],
                "surface": str(data.get("surface") or "")[:128],
                "faultKind": str(data.get("faultKind") or "")[:32],
                "faultAction": str(data.get("faultAction") or "")[:32],
            }
            with open(path, "a", encoding="utf-8") as f:
                f.write(_pa.json.dumps(row, ensure_ascii=False) + "\n")
            return {"ok": True, "path": str(path)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def open_patreon_page(self) -> None:
        import webbrowser

        from frontend.version_check import PATREON_URL

        webbrowser.open(PATREON_URL)

    def open_external_url(self, url: str) -> None:
        """Open an https link in the user's default browser (settings help links)."""
        import webbrowser

        u = str(url or "").strip()
        if u.startswith("https://"):
            webbrowser.open(u)

    def burst_desktop_confetti(self, client_x: float, client_y: float) -> None:
        """Fullscreen confetti burst at a point in the main window's client area."""
        if _pa.sys.platform != "win32" or not self._tk_root:
            return
        w = self._resolve_window()
        if not w:
            return
        screen_x = float(w.x) + float(client_x)
        screen_y = float(w.y) + float(client_y)
        from frontend.ui_web.confetti_overlay import schedule_desktop_confetti

        schedule_desktop_confetti(self._tk_root, screen_x, screen_y)

    def snip_screen(self) -> dict[str, Any]:
        """Open the Windows region snipper and attach the result to the chat.

        Accepted snips are saved under AppData only (tool_captures / snips) —
        never into the UEFN project folder (``.ducky/**`` is the only allowed
        project-side Ducky storage).
        """
        if _pa.sys.platform != "win32":
            return {"ok": False, "reason": "unsupported"}
        from frontend.ui_web.snip_overlay import snip_screen_interactive

        result = snip_screen_interactive(self._tk_root)
        if result.get("ok") and result.get("data_base64"):
            try:
                import base64 as _b64
                from datetime import datetime

                from frontend.ui_web.project_chats import get_conversations_dir
                from frontend.ui_web.tool_captures import copy_png_to_ducky_captures

                raw = _b64.b64decode(str(result["data_base64"]))
                name = f"snip-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')[:-3]}.png"

                snips_dir = get_conversations_dir().parent / "snips"
                snips_dir.mkdir(parents=True, exist_ok=True)
                appdata_path = snips_dir / name
                appdata_path.write_bytes(raw)
                result["name"] = name
                result["capture_path"] = str(appdata_path)

                # AppData tool_captures (never project Saved/).
                capture_path = copy_png_to_ducky_captures(raw, prefix="snip", filename=name)
                result["path"] = capture_path or str(appdata_path)
            except Exception:
                pass  # disk copy is best-effort; the composer attachment still works
        return result

    def hide_window(self) -> None:
        if self._hide_callback:
            self._hide_callback()

    def pick_project_path(self) -> str | None:
        # Prefer pywebview's native dialog: it runs on the GUI thread and is safe to call from
        # this JS-API worker thread. The Tk route can't (Tk is owned by the pump thread, and
        # cross-thread ``root.after`` raises "main thread is not in main loop").
        win = self._window
        if win is not None:
            return self._pick_project_path_webview(win)
        return self._pick_project_path_standalone()

    def _pick_project_path_webview(self, win: Any) -> str | None:
        import webview

        try:
            folder_type = webview.FileDialog.FOLDER
            open_type = webview.FileDialog.OPEN
        except AttributeError:  # older pywebview
            folder_type = getattr(webview, "FOLDER_DIALOG", 20)
            open_type = getattr(webview, "OPEN_DIALOG", 10)

        try:
            picked = win.create_file_dialog(folder_type)
            if picked:
                return str(_pa.resolve_uefn_project_root(_pa.Path(picked[0])))
            picked = win.create_file_dialog(
                open_type,
                file_types=("UEFN project (*.uefnproject)", "All files (*.*)"),
            )
            if picked:
                return str(_pa.resolve_uefn_project_root(_pa.Path(picked[0])))
        except Exception:
            return None
        return None

    def _pick_project_path_dialog(self, root: Any) -> str | None:
        from tkinter import filedialog

        d = filedialog.askdirectory(title="UEFN project folder", parent=root)
        if d:
            return str(_pa.resolve_uefn_project_root(_pa.Path(d)))
        f = filedialog.askopenfilename(
            title="Or pick a .uefnproject file",
            parent=root,
            filetypes=[("UEFN", "*.uefnproject"), ("All", "*.*")],
        )
        if f:
            return str(_pa.resolve_uefn_project_root(_pa.Path(f)))
        return None

    def _pick_project_path_standalone(self) -> str | None:
        try:
            import tkinter as tk

            root = tk.Tk()
            root.withdraw()
            try:
                return self._pick_project_path_dialog(root)
            finally:
                root.destroy()
        except Exception:
            return None

    def open_appdata(self) -> None:
        d = _pa.default_app_data_dir()
        d.mkdir(parents=True, exist_ok=True)
        _pa.os.startfile(str(d))  # type: ignore[attr-defined]

    def _pick_save_file_webview(
        self,
        win: Any,
        *,
        default_name: str,
        file_types: tuple[str, ...],
    ) -> str | None:
        import webview

        try:
            save_type = webview.FileDialog.SAVE
        except AttributeError:
            save_type = getattr(webview, "SAVE_DIALOG", 30)
        try:
            picked = win.create_file_dialog(
                save_type,
                save_filename=default_name,
                file_types=file_types,
            )
            if picked:
                return str(picked if isinstance(picked, str) else picked[0])
        except ValueError:
            # Invalid file_types filter — retry with a safe fallback.
            try:
                picked = win.create_file_dialog(
                    save_type,
                    save_filename=default_name,
                    file_types=("All files (*.*)",),
                )
                if picked:
                    return str(picked if isinstance(picked, str) else picked[0])
            except Exception:
                return None
        except Exception:
            return None
        return None

    def _pick_open_file_webview(self, win: Any, *, file_types: tuple[str, ...]) -> str | None:
        import webview

        try:
            open_type = webview.FileDialog.OPEN
        except AttributeError:
            open_type = getattr(webview, "OPEN_DIALOG", 10)
        try:
            picked = win.create_file_dialog(open_type, file_types=file_types)
            if picked:
                return str(picked[0] if isinstance(picked, (list, tuple)) else picked)
        except ValueError:
            try:
                picked = win.create_file_dialog(open_type, file_types=("All files (*.*)",))
                if picked:
                    return str(picked[0] if isinstance(picked, (list, tuple)) else picked)
            except Exception:
                return None
        except Exception:
            return None
        return None

    def open_path_in_explorer(self, path: str) -> None:
        import subprocess
        from pathlib import Path

        target = _pa.Path(path).expanduser()
        if not target.exists():
            target = target.parent
        if not target.exists():
            return
        if target.is_file():
            subprocess.run(["explorer", "/select,", str(target)], check=False)
        else:
            _pa.os.startfile(str(target))  # type: ignore[attr-defined]

    def open_project_path_in_explorer(self, relative_path: str) -> None:
        from frontend.ui_web.project_files import resolve_project_file_path

        self.open_path_in_explorer(resolve_project_file_path(relative_path))

    def terminal_spawn(
        self,
        shell: str = "bash",
        cwd: str = "",
        title: str = "",
    ) -> dict[str, Any]:
        from frontend.ui_web.terminal import get_terminal_manager

        workdir = _pa._normalize_project_path(cwd) if cwd.strip() else ""
        return get_terminal_manager().spawn(shell=shell, cwd=workdir or None, title=title)

    def terminal_kill(self, session_id: str) -> dict[str, Any]:
        from frontend.ui_web.terminal import get_terminal_manager

        return get_terminal_manager().kill(session_id.strip())

    def terminal_busy(self, session_id: str) -> dict[str, Any]:
        from frontend.ui_web.terminal import get_terminal_manager

        return get_terminal_manager().busy_state(session_id.strip())

    def terminal_list(self) -> dict[str, Any]:
        from frontend.ui_web.terminal import get_terminal_manager

        return {"sessions": get_terminal_manager().list_sessions()}

    def terminal_write(self, session_id: str, data: str) -> dict[str, Any]:
        from frontend.ui_web.terminal import get_terminal_manager

        return get_terminal_manager().write(session_id.strip(), data)

    def terminal_resize(self, session_id: str, cols: int, rows: int) -> dict[str, Any]:
        from frontend.ui_web.terminal import get_terminal_manager

        return get_terminal_manager().resize(session_id.strip(), cols, rows)

    def terminal_request_command(
        self,
        session_id: str,
        command: str,
        source: str = "",
        conv_id: str = "",
    ) -> dict[str, Any]:
        from frontend.ui_web.terminal import get_terminal_manager

        return get_terminal_manager().request_command(
            session_id.strip(),
            command,
            source=source,
            conv_id=conv_id,
        )

    def terminal_approve_command(self, request_id: str) -> dict[str, Any]:
        from frontend.ui_web.terminal import get_terminal_manager

        return get_terminal_manager().approve_command(request_id.strip())

    def terminal_reject_command(self, request_id: str, reason: str = "") -> dict[str, Any]:
        from frontend.ui_web.terminal import get_terminal_manager

        return get_terminal_manager().reject_command(request_id.strip(), reason=reason or "rejected by user")

    def terminal_read_output(self, session_id: str, max_chars: int = 8000) -> dict[str, Any]:
        from frontend.ui_web.terminal import get_terminal_manager

        return get_terminal_manager().read_output(session_id.strip(), max_chars=max_chars)

    def exit_all(self) -> bool:
        from frontend.ui_web.shutdown import abort_and_quit

        holder = getattr(self, "_window_holder", None)
        if not isinstance(holder, dict):
            holder = {}
        abort_and_quit(api=self, tk_root=self._tk_root, window_holder=holder)
        return True
