"""Single process-wide tk root for Toplevel windows."""

import tkinter as tk

import unreal


def get_tk_root() -> tk.Tk:
    if hasattr(unreal, "_mcp_tk_root") and unreal._mcp_tk_root is not None:
        try:
            unreal._mcp_tk_root.winfo_exists()
            return unreal._mcp_tk_root
        except Exception:
            unreal._mcp_tk_root = None

    try:
        existing = tk._default_root  # noqa: SLF001
        if existing is not None and existing.winfo_exists():
            unreal._mcp_tk_root = existing
            return existing
    except Exception:
        pass

    root = tk.Tk()
    root.withdraw()
    unreal._mcp_tk_root = root
    return root
