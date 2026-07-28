"""Tkinter status popup (daemon thread) — same layout/behavior as classic uefn_listener MCPStatusWindow."""

import threading
import time
import tkinter as tk
from typing import Dict, Optional

import unreal

from listener import config
from listener.main_thread import run_on_main_thread
from listener.state import metrics
from listener.tk_root import get_tk_root


class MCPStatusWindow:
    """Compact floating status window for the MCP listener."""

    BG = "#1e1e1e"
    BG_SECTION = "#252525"
    FG = "#cccccc"
    FG_DIM = "#777777"
    GREEN = "#4ec94e"
    RED = "#e74c4c"
    YELLOW = "#e0c050"
    FONT = ("Segoe UI", 9)
    FONT_BOLD = ("Segoe UI", 10, "bold")
    FONT_BIG = ("Segoe UI", 12)
    UPDATE_MS = 1000

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._window: Optional[tk.Toplevel] = None
        self._closing = False
        self._after_update_id: Optional[str] = None
        self._labels: Dict[str, tk.Label] = {}
        self._listener_dot: Optional[tk.Label] = None
        self._listener_text: Optional[tk.Label] = None
        self._client_dot: Optional[tk.Label] = None
        self._client_text: Optional[tk.Label] = None
        self._btn_toggle: Optional[tk.Button] = None
        self._port_var: Optional[tk.StringVar] = None
        self._port_entry: Optional[tk.Entry] = None

    def start(self) -> None:
        # If the user closed the Toplevel, we must have ended mainloop via root.quit();
        # otherwise the thread stays alive with _window is None and we would never show again.
        if self._thread and self._thread.is_alive() and self._window is not None:
            try:
                self._window.lift()
                self._window.focus_force()
            except Exception:
                pass
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        self._closing = False
        try:
            root = get_tk_root()
            self._create_window(root)
            root.mainloop()
        except Exception as e:
            try:
                unreal.log_error(f"[MCP] Status window failed: {e}")
            except Exception:
                pass
        finally:
            self._window = None
            self._after_update_id = None

    def _create_window(self, root: tk.Tk) -> None:
        window = tk.Toplevel(root)
        self._window = window
        self._labels = {}
        window.title("You can close this window")
        window.geometry("260x295")
        window.attributes("-topmost", True)
        window.configure(bg=self.BG)
        window.resizable(False, False)

        title_frame = tk.Frame(window, bg=self.BG)
        title_frame.pack(fill="x", padx=12, pady=(10, 2))
        tk.Label(title_frame, text="UEFN Ducky Listener", font=self.FONT_BIG, fg=self.FG, bg=self.BG).pack(side="left")
        tk.Label(title_frame, text=f"v{config.PROTOCOL_VERSION}", font=self.FONT, fg=self.FG_DIM, bg=self.BG).pack(side="right")

        hdr = tk.Frame(window, bg=self.BG)
        hdr.pack(fill="x", padx=12, pady=(4, 2))

        row1 = tk.Frame(hdr, bg=self.BG)
        row1.pack(fill="x")
        self._listener_dot = tk.Label(row1, text="\u25cf", font=self.FONT, fg=self.GREEN, bg=self.BG)
        self._listener_dot.pack(side="left")
        self._listener_text = tk.Label(row1, text="Listener: Running", font=self.FONT_BOLD, fg=self.FG, bg=self.BG)
        self._listener_text.pack(side="left", padx=(4, 0))

        row2 = tk.Frame(hdr, bg=self.BG)
        row2.pack(fill="x", pady=(2, 0))
        self._client_dot = tk.Label(row2, text="\u25cf", font=self.FONT, fg=self.FG_DIM, bg=self.BG)
        self._client_dot.pack(side="left")
        self._client_text = tk.Label(row2, text="MCP Server: Connecting...", font=self.FONT, fg=self.FG_DIM, bg=self.BG)
        self._client_text.pack(side="left", padx=(4, 0))

        tk.Frame(window, bg="#333333", height=1).pack(fill="x", padx=12, pady=4)

        info = tk.Frame(window, bg=self.BG)
        info.pack(fill="x", padx=12, pady=2)
        info.columnconfigure(1, weight=1)

        tk.Label(info, text="Port", font=self.FONT, fg=self.FG_DIM, bg=self.BG, anchor="w").grid(
            row=0, column=0, sticky="w", pady=1
        )
        self._port_var = tk.StringVar(value=str(config.listener_port_hint()))
        self._port_entry = tk.Entry(
            info, textvariable=self._port_var, font=self.FONT, width=7,
            bg="#333333", fg=self.FG, insertbackground=self.FG,
            disabledbackground=self.BG, disabledforeground=self.FG,
            relief="flat", justify="right", state="disabled",
        )
        self._port_entry.grid(row=0, column=1, sticky="e", padx=(10, 0), pady=1)

        rows = [
            ("Uptime", "uptime"),
            ("Requests", "requests"),
            ("Errors", "errors"),
            ("Last cmd", "last_cmd"),
            ("Avg time", "avg_time"),
        ]
        for i, (label_text, key) in enumerate(rows, start=1):
            tk.Label(info, text=label_text, font=self.FONT, fg=self.FG_DIM, bg=self.BG, anchor="w").grid(
                row=i, column=0, sticky="w", pady=1
            )
            lbl = tk.Label(info, text="\u2014", font=self.FONT, fg=self.FG, bg=self.BG, anchor="e")
            lbl.grid(row=i, column=1, sticky="e", padx=(10, 0), pady=1)
            self._labels[key] = lbl

        tk.Frame(window, bg="#333333", height=1).pack(fill="x", padx=12, pady=4)

        btn_frame = tk.Frame(window, bg=self.BG)
        btn_frame.pack(fill="x", padx=12, pady=(2, 8))

        btn_cfg = dict(bg="#3c3c3c", fg=self.FG, activebackground="#4a4a4a", activeforeground=self.FG,
                       relief="flat", font=self.FONT, padx=12, pady=2, cursor="hand2")

        self._btn_toggle = tk.Button(btn_frame, text="Stop", command=self._on_toggle, **btn_cfg)
        self._btn_toggle.pack(side="left")

        tk.Button(btn_frame, text="Restart", command=self._on_restart, **btn_cfg).pack(side="left", padx=(6, 0))

        self._update()
        window.protocol("WM_DELETE_WINDOW", self._on_close)

    def _update(self) -> None:
        if self._closing or not self._window:
            return

        running = unreal._mcp_server is not None

        if self._listener_dot:
            self._listener_dot.configure(fg=self.GREEN if running else self.RED)
        if self._listener_text:
            self._listener_text.configure(text="Listener: Running" if running else "Listener: Stopped")
        if self._btn_toggle:
            self._btn_toggle.configure(text="Stop" if running else "Start")

        last_ping = metrics.get("last_client_ping", 0.0)
        if last_ping > 0:
            ago = int(time.time() - last_ping)
            if ago < 15:
                client_color = self.GREEN
                client_text = "MCP Server: Connected"
                client_fg = self.FG
            else:
                if ago < 60:
                    ago_str = f"{ago}s ago"
                elif ago < 3600:
                    ago_str = f"{ago // 60}m ago"
                else:
                    ago_str = f"{ago // 3600}h ago"
                client_color = self.FG_DIM
                client_text = f"MCP Server: Lost {ago_str}"
                client_fg = self.FG_DIM
        elif running:
            client_color = self.YELLOW
            client_text = "MCP Server: Connecting..."
            client_fg = self.FG_DIM
        else:
            client_color = self.FG_DIM
            client_text = "MCP Server: Not connected"
            client_fg = self.FG_DIM

        if self._client_dot:
            self._client_dot.configure(fg=client_color)
        if self._client_text:
            self._client_text.configure(text=client_text, fg=client_fg)

        if self._port_entry:
            if running:
                self._port_entry.configure(state="disabled")
                self._port_var.set(str(unreal._mcp_bound_port))
            else:
                self._port_entry.configure(state="normal")

        if running and metrics["started_at"] > 0:
            uptime = int(time.time() - metrics["started_at"])
            h, rem = divmod(uptime, 3600)
            m, s = divmod(rem, 60)
            self._labels["uptime"].configure(text=f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s")
        else:
            self._labels["uptime"].configure(text="\u2014")

        self._labels["requests"].configure(text=str(metrics["total_requests"]))

        errs = metrics["total_errors"]
        self._labels["errors"].configure(text=str(errs), fg=self.RED if errs > 0 else self.FG)

        last = metrics["last_command"]
        if last and metrics["last_request_at"] > 0:
            ago = int(time.time() - metrics["last_request_at"])
            if ago < 60:
                ago_str = f"{ago}s ago"
            elif ago < 3600:
                ago_str = f"{ago // 60}m ago"
            else:
                ago_str = f"{ago // 3600}h ago"
            self._labels["last_cmd"].configure(text=f"{last} ({ago_str})")
        else:
            self._labels["last_cmd"].configure(text="\u2014")

        times = metrics["response_times_ms"]
        if times:
            avg = sum(times) / len(times)
            self._labels["avg_time"].configure(text=f"{avg:.1f} ms")
        else:
            self._labels["avg_time"].configure(text="\u2014")

        try:
            if self._window and not self._closing:
                self._after_update_id = self._window.after(self.UPDATE_MS, self._update)
        except tk.TclError:
            pass

    def _on_toggle(self) -> None:
        from listener import runtime

        if unreal._mcp_server is not None:
            run_on_main_thread(runtime.stop_listener)
        else:
            try:
                port = int(self._port_var.get())
            except (ValueError, TypeError):
                port = 0
            # Window is already open on this thread — do not spawn another status mainloop (matches monolithic uefn_listener).
            run_on_main_thread(lambda: runtime.start_listener(port=port, show_status=False))

    def _on_restart(self) -> None:
        from listener import runtime

        run_on_main_thread(runtime.restart_listener)

    def _on_close(self) -> None:
        self._closing = True
        if self._after_update_id and self._window:
            try:
                self._window.after_cancel(self._after_update_id)
            except Exception:
                pass
        self._after_update_id = None
        root: Optional[tk.Tk] = None
        w = self._window
        self._window = None
        if w is not None:
            try:
                root = w.master  # type: ignore[assignment]
                w.destroy()
            except Exception:
                pass
        if root is not None:
            try:
                root.quit()
            except Exception:
                pass
