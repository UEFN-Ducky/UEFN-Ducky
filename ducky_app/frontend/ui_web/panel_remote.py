"""Remote access methods of PanelApi (bound in PanelApiStoreMixin): browser access on/off,
the sessions on this PC, the devices it approved, and the handshake to another UEFN Ducky.
Each takes the panel as ``self``."""

from __future__ import annotations

from typing import Any


def remote_status(self) -> dict[str, Any]:
    from frontend import remote_trust
    from frontend.settings import PanelSettings
    from frontend.ui_web.panel_httpd import remote_session_count, remote_session_summaries

    enabled = bool(getattr(PanelSettings.load(), "remote_access", False))
    # Remote Ducky connects peer to peer: nothing to start, so on is ready.
    return {
        "ok": True,
        "enabled": enabled,
        "running": enabled,
        "error": "",
        "site_update_pending": False,
        "sessions": remote_session_count(),
        "session_list": remote_session_summaries(),
        # Approved devices for the Account panel's list (Remove → remote_device_remove).
        "devices": remote_devices_list(self)["devices"],
        "key_protection": remote_trust.key_protection(),
    }


def remote_set_enabled(self, enabled: bool = False) -> dict[str, Any]:
    from frontend.duckyos_account import publish_device_presence
    from frontend.settings import PanelSettings
    from frontend.ui_web.panel_api import _save_panel_settings

    s = PanelSettings.load()
    s.remote_access = bool(enabled)
    _save_panel_settings(s)
    if not s.remote_access:
        from frontend.ui_web.panel_httpd import kick_all_remote

        kick_all_remote()
    publish_device_presence(live=s.remote_access)
    return self.remote_status()


def remote_sign_out_all(self) -> dict[str, Any]:
    from frontend.ui_web.panel_httpd import kick_all_remote

    kick_all_remote()
    return self.remote_status()


# Remote devices: only this PC's own window may approve or remove one (REMOTE_DENY).
def remote_pair_decide(self, viewer_id: str = "", allow: bool = False) -> dict[str, Any]:
    """Allow or deny one waiting device (the pair modal's buttons)."""
    from frontend import remote_trust

    if not remote_trust.decide(str(viewer_id or ""), bool(allow)):
        return {"ok": False, "error": "That request expired. Ask the device to connect again."}
    return {"ok": True}


def remote_devices_list(self) -> dict[str, Any]:
    """Approved devices, devices waiting for approval, and how this PC's key is kept."""
    from frontend import remote_trust

    rows = [{"id": vid, "label": r.get("label", ""), "added": r.get("added", 0)}
            for vid, r in remote_trust.approved().items()]
    return {"ok": True, "devices": rows, "pending": remote_trust.pending_requests(),
            "protection": remote_trust.key_protection()}


def remote_device_remove(self, viewer_id: str = "") -> dict[str, Any]:
    from frontend import remote_trust

    return {"ok": remote_trust.revoke(str(viewer_id or ""))}


def ducky_link_rtc_connect(self, args: dict | None = None) -> dict[str, Any]:
    """Handshake for a window showing another UEFN Ducky: the page's offer goes to
    that Ducky sealed; its answer comes back. ``{"answer", "version", "protocol"}``."""
    from frontend import remote_trust
    from frontend.ducky_link import call

    raw = args if isinstance(args, dict) else {}
    to = str(raw.get("to") or "")
    signed = remote_trust.sign_offer({k: raw.get(k) for k in ("session", "offer", "ice", "protocol")})
    out = call(to, "rtc_connect", signed, timeout=45.0)
    result = out.get("result") if isinstance(out.get("result"), dict) else {}
    if not out.get("ok"):
        # First time, ducky_link.call asked the other PC to approve this one (code in the error).
        raise RuntimeError(str(out.get("error") or "That UEFN Ducky didn't answer."))
    if not remote_trust.check_answer(remote_trust.pinned_pc(to), signed, result):
        raise RuntimeError("That UEFN Ducky's answer could not be verified. Pair it again.")
    return result
